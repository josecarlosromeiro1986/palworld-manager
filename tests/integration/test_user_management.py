from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select

from app.auth.passwords import verify_password
from app.auth.roles import UserRole
from app.auth.service import InvalidUsernameError, create_administrator
from app.auth.sessions import issue_session
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, SessionRecord, User
from app.users.service import (
    LastActiveAdministratorError,
    SelfManagementError,
    UsernameAlreadyExistsError,
    change_user_role,
    create_user,
    reset_user_password,
    set_user_active,
    update_own_password,
)


@pytest.fixture
def users_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def _administrator(engine: Engine) -> int:
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        return create_administrator(session, "Admin", "senha-ficticia").id


def test_create_user_preserves_display_and_enforces_casefold_uniqueness(
    users_engine: Engine,
) -> None:
    administrator_id = _administrator(users_engine)
    factory = create_session_factory(users_engine)
    with session_scope(factory) as session:
        created = create_user(
            session,
            " Operador ",
            "senha-temporaria",
            UserRole.USER,
            actor_user_id=administrator_id,
        )
        created_id = created.id

    with pytest.raises(UsernameAlreadyExistsError), session_scope(factory) as session:
        create_user(
            session,
            "OPERADOR",
            "outra-senha",
            UserRole.USER,
            actor_user_id=administrator_id,
        )
    with pytest.raises(InvalidUsernameError), session_scope(factory) as session:
        create_user(
            session,
            "usuario\n",
            "outra-senha",
            UserRole.USER,
            actor_user_id=administrator_id,
        )

    with session_scope(factory) as session:
        user = session.get_one(User, created_id)
        event = session.scalar(select(AuditEvent).where(AuditEvent.action == "USER_CREATED"))
        assert user.username == "Operador"
        assert user.username_key == "operador"
        assert user.role == UserRole.USER.value
        assert user.password_change_required is True
        assert verify_password("senha-temporaria", user.password_hash)
        assert event is not None
        assert event.details == {"role": "USER"}


def test_role_status_and_reset_revoke_sessions_and_protect_self(
    users_engine: Engine,
) -> None:
    administrator_id = _administrator(users_engine)
    factory = create_session_factory(users_engine)
    with session_scope(factory) as session:
        user = create_user(
            session,
            "operador",
            "senha-temporaria",
            UserRole.USER,
            actor_user_id=administrator_id,
        )
        user_id = user.id
        issue_session(session, user)

    with session_scope(factory) as session:
        change_user_role(
            session,
            user_id,
            UserRole.ADMIN,
            actor_user_id=administrator_id,
        )
    with session_scope(factory) as session:
        record = session.scalar(select(SessionRecord).where(SessionRecord.user_id == user_id))
        assert record is not None and record.revoked_at is not None
        issue_session(session, session.get_one(User, user_id))

    with session_scope(factory) as session:
        set_user_active(session, user_id, False, actor_user_id=administrator_id)
    with session_scope(factory) as session:
        user = session.get_one(User, user_id)
        password_hash = user.password_hash
        assert user.is_active is False
        records = tuple(
            session.scalars(select(SessionRecord).where(SessionRecord.user_id == user_id))
        )
        assert all(record.revoked_at is not None for record in records)
    with session_scope(factory) as session:
        reactivated = set_user_active(
            session,
            user_id,
            True,
            actor_user_id=administrator_id,
        )
        assert reactivated.password_hash == password_hash
        issue_session(session, reactivated)
    with session_scope(factory) as session:
        reset_user_password(
            session,
            user_id,
            "nova-senha-temporaria",
            actor_user_id=administrator_id,
        )
    with session_scope(factory) as session:
        user = session.get_one(User, user_id)
        records = tuple(
            session.scalars(select(SessionRecord).where(SessionRecord.user_id == user_id))
        )
        assert user.password_change_required is True
        assert verify_password("nova-senha-temporaria", user.password_hash)
        assert all(record.revoked_at is not None for record in records)

    with pytest.raises(SelfManagementError), session_scope(factory) as session:
        reset_user_password(
            session,
            administrator_id,
            "senha-temporaria",
            actor_user_id=administrator_id,
        )


def test_last_active_administrator_cannot_be_demoted_or_deactivated(
    users_engine: Engine,
) -> None:
    administrator_id = _administrator(users_engine)
    factory = create_session_factory(users_engine)
    with session_scope(factory) as session:
        second = create_user(
            session,
            "segundo-admin",
            "senha-temporaria",
            UserRole.ADMIN,
            actor_user_id=administrator_id,
        )
        second_id = second.id
    with session_scope(factory) as session:
        set_user_active(session, administrator_id, False, actor_user_id=second_id)

    with pytest.raises(LastActiveAdministratorError), session_scope(factory) as session:
        change_user_role(
            session,
            second_id,
            UserRole.USER,
            actor_user_id=administrator_id,
        )
    with pytest.raises(LastActiveAdministratorError), session_scope(factory) as session:
        set_user_active(session, second_id, False, actor_user_id=administrator_id)


def test_own_password_change_clears_temporary_flag_and_revokes_sessions(
    users_engine: Engine,
) -> None:
    administrator_id = _administrator(users_engine)
    factory = create_session_factory(users_engine)
    with session_scope(factory) as session:
        user = create_user(
            session,
            "operador",
            "senha-temporaria",
            UserRole.USER,
            actor_user_id=administrator_id,
        )
        user_id = user.id
        issue_session(session, user)
    with session_scope(factory) as session:
        update_own_password(session, user_id, "senha-definitiva")

    with session_scope(factory) as session:
        user = session.get_one(User, user_id)
        record = session.scalar(select(SessionRecord).where(SessionRecord.user_id == user_id))
        assert user.password_change_required is False
        assert verify_password("senha-definitiva", user.password_hash)
        assert record is not None and record.revoked_at is not None
