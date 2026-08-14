from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select

from app.backups.scheduler import schedule_daily_backup
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, Job


@pytest.fixture
def scheduler_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def test_daily_scheduler_uses_configured_timezone_and_runs_once(
    scheduler_engine: Engine,
) -> None:
    factory = create_session_factory(scheduler_engine)
    before = datetime(2026, 8, 14, 6, 59, tzinfo=UTC)
    due = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    with session_scope(factory) as session:
        assert not schedule_daily_backup(session, now=before)
    with session_scope(factory) as session:
        assert schedule_daily_backup(session, now=due)
    with session_scope(factory) as session:
        session.scalar(select(Job)).status = "SUCCEEDED"  # type: ignore[union-attr]
    with session_scope(factory) as session:
        assert not schedule_daily_backup(session, now=due)
        assert len(tuple(session.scalars(select(Job)))) == 1


def test_daily_scheduler_honors_disabled_setting(scheduler_engine: Engine) -> None:
    factory = create_session_factory(scheduler_engine)
    with session_scope(factory) as session:
        session.add(AppSetting(key="backup_enabled", value=False))
    with session_scope(factory) as session:
        assert not schedule_daily_backup(
            session,
            now=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        )
