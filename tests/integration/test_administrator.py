from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select, text

from app.auth.passwords import verify_password
from app.auth.service import (
    AdministratorAlreadyExistsError,
    AdministratorNotFoundError,
    create_administrator,
    reset_administrator_password,
)
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import User


@pytest.fixture
def migrated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def test_create_administrator_persists_only_argon2id_hash(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    plaintext_password = "senha-ficticia-inicial"

    with session_scope(factory) as session:
        administrator = create_administrator(session, " admin ", plaintext_password)
        administrator_id = administrator.id

    with session_scope(factory) as session:
        stored = session.get(User, administrator_id)
        assert stored is not None
        assert stored.username == "admin"
        assert stored.password_hash.startswith("$argon2id$")
        assert plaintext_password not in stored.password_hash
        assert verify_password(plaintext_password, stored.password_hash)

    with migrated_engine.connect() as connection:
        database_contents = connection.execute(
            text("SELECT CAST(password_hash AS TEXT) FROM users")
        ).scalar_one()
    assert plaintext_password not in database_contents


def test_v1_rejects_a_second_administrator(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)

    with session_scope(factory) as session:
        create_administrator(session, "admin", "primeira-senha")

    with pytest.raises(AdministratorAlreadyExistsError), session_scope(factory) as session:
        create_administrator(session, "outro-admin", "segunda-senha")


def test_reset_administrator_password_replaces_hash(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)

    with session_scope(factory) as session:
        administrator = create_administrator(session, "admin", "senha-antiga")
        old_hash = administrator.password_hash

    with session_scope(factory) as session:
        reset_administrator_password(session, "admin", "senha-nova")

    with session_scope(factory) as session:
        stored = session.scalar(select(User).where(User.username == "admin"))
        assert stored is not None
        assert stored.password_hash != old_hash
        assert not verify_password("senha-antiga", stored.password_hash)
        assert verify_password("senha-nova", stored.password_hash)


def test_reset_rejects_unknown_administrator(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)

    with pytest.raises(AdministratorNotFoundError), session_scope(factory) as session:
        reset_administrator_password(session, "ausente", "senha-nova")
