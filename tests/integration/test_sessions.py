from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select

from app.auth.service import create_administrator
from app.auth.sessions import IssuedSession, issue_session, resolve_session, revoke_session
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import SessionRecord


@pytest.fixture
def migrated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def _issue_session(migrated_engine: Engine, now: datetime) -> IssuedSession:
    factory = create_session_factory(migrated_engine)
    with session_scope(factory) as session:
        administrator = create_administrator(session, "admin", "senha-ficticia")
        return issue_session(session, administrator, now=now)


def test_session_stores_only_token_hashes_and_updates_activity(migrated_engine: Engine) -> None:
    issued_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    issued = _issue_session(migrated_engine, issued_at)
    factory = create_session_factory(migrated_engine)

    with session_scope(factory) as session:
        stored = session.scalar(select(SessionRecord))
        assert stored is not None
        assert issued.session_token not in stored.token_hash
        assert issued.csrf_token not in stored.csrf_token_hash

        principal = resolve_session(
            session,
            issued.session_token,
            now=issued_at + timedelta(minutes=30),
        )
        assert principal is not None
        assert principal.username == "admin"

    with session_scope(factory) as session:
        stored = session.scalar(select(SessionRecord))
        assert stored is not None
        assert stored.last_seen_at == (issued_at + timedelta(minutes=30)).replace(tzinfo=None)


def test_session_expires_after_one_hour_of_inactivity(migrated_engine: Engine) -> None:
    issued_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    issued = _issue_session(migrated_engine, issued_at)
    factory = create_session_factory(migrated_engine)

    with session_scope(factory) as session:
        assert (
            resolve_session(
                session,
                issued.session_token,
                now=issued_at + timedelta(hours=1),
            )
            is None
        )

    with session_scope(factory) as session:
        stored = session.scalar(select(SessionRecord))
        assert stored is not None
        assert stored.revoked_at is not None


def test_session_never_exceeds_eight_hours_even_with_activity(migrated_engine: Engine) -> None:
    issued_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    issued = _issue_session(migrated_engine, issued_at)
    factory = create_session_factory(migrated_engine)

    for half_hour in range(1, 16):
        with session_scope(factory) as session:
            assert (
                resolve_session(
                    session,
                    issued.session_token,
                    now=issued_at + timedelta(minutes=30 * half_hour),
                )
                is not None
            )

    with session_scope(factory) as session:
        assert (
            resolve_session(
                session,
                issued.session_token,
                now=issued_at + timedelta(hours=8),
            )
            is None
        )


def test_revoked_session_cannot_be_resolved(migrated_engine: Engine) -> None:
    issued_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    issued = _issue_session(migrated_engine, issued_at)
    factory = create_session_factory(migrated_engine)

    with session_scope(factory) as session:
        stored = session.scalar(select(SessionRecord))
        assert stored is not None
        revoke_session(session, stored.id, now=issued_at + timedelta(minutes=1))

    with session_scope(factory) as session:
        assert (
            resolve_session(
                session,
                issued.session_token,
                now=issued_at + timedelta(minutes=2),
            )
            is None
        )
