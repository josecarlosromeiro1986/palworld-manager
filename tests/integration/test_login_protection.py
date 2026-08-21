from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.login_protection import (
    LOGIN_BLOCK_DURATION,
    LoginResult,
    attempt_administrator_login,
)
from app.auth.service import create_administrator
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, LoginAttempt, NotificationEvent


@dataclass(frozen=True)
class LoginProtectionContext:
    engine: Engine
    factory: sessionmaker[Session]


@pytest.fixture
def login_protection_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[LoginProtectionContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")

    context = LoginProtectionContext(engine=engine, factory=factory)
    yield context
    engine.dispose()


def _attempt(
    context: LoginProtectionContext,
    *,
    username: str = "admin",
    password: str = "senha-incorreta",
    source_address: str = "198.51.100.10",
    now: datetime,
) -> LoginResult:
    with session_scope(context.factory) as session:
        return attempt_administrator_login(
            session,
            username,
            password,
            source_address,
            now=now,
        )


def test_fifth_consecutive_failure_blocks_login_and_is_audited(
    login_protection_context: LoginProtectionContext,
) -> None:
    base_time = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    for offset in range(4):
        result = _attempt(
            login_protection_context,
            now=base_time + timedelta(seconds=offset),
        )
        assert not result.blocked

    blocked = _attempt(login_protection_context, now=base_time + timedelta(seconds=4))
    still_blocked = _attempt(
        login_protection_context,
        password="senha-ficticia",
        now=base_time + timedelta(seconds=5),
    )

    assert blocked.blocked_until == base_time + timedelta(seconds=4) + LOGIN_BLOCK_DURATION
    assert still_blocked.blocked_until == blocked.blocked_until

    with session_scope(login_protection_context.factory) as session:
        attempts = list(session.scalars(select(LoginAttempt).order_by(LoginAttempt.id)))
        events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
        notifications = list(session.scalars(select(NotificationEvent)))

    assert len(attempts) == 6
    assert all(attempt.source_address == "198.51.100.10" for attempt in attempts)
    assert [event.action for event in events].count("LOGIN") == 6
    blocked_events = [event for event in events if event.action == "LOGIN_BLOCKED"]
    assert len(blocked_events) == 1
    assert blocked_events[0].result == "SUCCESS"
    assert blocked_events[0].origin == "SYSTEM"
    assert [event.event_type for event in notifications] == ["LOGIN_BLOCKED"]
    assert notifications[0].job_id is None
    assert "senha-incorreta" not in repr(events)
    assert "senha-ficticia" not in repr(events)


def test_successful_login_resets_consecutive_failure_count(
    login_protection_context: LoginProtectionContext,
) -> None:
    base_time = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    for offset in range(4):
        _attempt(login_protection_context, now=base_time + timedelta(seconds=offset))

    success = _attempt(
        login_protection_context,
        password="senha-ficticia",
        now=base_time + timedelta(seconds=4),
    )
    assert success.authenticated

    for offset in range(5, 9):
        result = _attempt(login_protection_context, now=base_time + timedelta(seconds=offset))
        assert not result.blocked

    blocked = _attempt(login_protection_context, now=base_time + timedelta(seconds=9))
    assert blocked.blocked


def test_expired_block_restarts_count_from_zero(
    login_protection_context: LoginProtectionContext,
) -> None:
    base_time = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    blocked: LoginResult | None = None
    for offset in range(5):
        blocked = _attempt(
            login_protection_context,
            now=base_time + timedelta(seconds=offset),
        )

    assert blocked is not None
    assert blocked.blocked_until is not None
    success = _attempt(
        login_protection_context,
        password="senha-ficticia",
        now=blocked.blocked_until,
    )
    assert success.authenticated


def test_block_groups_sources_by_username_without_affecting_another_user(
    login_protection_context: LoginProtectionContext,
) -> None:
    base_time = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    other: LoginResult | None = None
    for offset in range(5):
        other = _attempt(
            login_protection_context,
            username="outro",
            source_address=f"198.51.100.{offset + 10}",
            now=base_time + timedelta(seconds=offset),
        )

    administrator = _attempt(
        login_protection_context,
        password="senha-ficticia",
        now=base_time + timedelta(seconds=5),
    )
    assert other is not None
    assert other.blocked
    assert administrator.authenticated


def test_concurrent_failures_cannot_bypass_limit(
    login_protection_context: LoginProtectionContext,
) -> None:
    attempt_time = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def fail_login(_: int) -> LoginResult:
        return _attempt(login_protection_context, now=attempt_time)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(fail_login, range(5)))

    assert sum(result.blocked for result in results) == 1
    with session_scope(login_protection_context.factory) as session:
        attempts = list(session.scalars(select(LoginAttempt)))
        block_events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == "LOGIN_BLOCKED"))
        )
    assert len(attempts) == 5
    assert len(block_events) == 1
