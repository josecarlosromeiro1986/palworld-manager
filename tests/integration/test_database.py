from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import User

EXPECTED_TABLES = {
    "alembic_version",
    "app_settings",
    "audit_events",
    "backup_records",
    "ban_history",
    "jobs",
    "login_attempts",
    "maintenance_locks",
    "notification_events",
    "sessions",
    "users",
    "worker_heartbeats",
}


@pytest.fixture
def migrated_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def test_new_database_is_created_only_by_migrations(migrated_engine: Engine) -> None:
    assert set(inspect(migrated_engine).get_table_names()) == EXPECTED_TABLES
    command.check(Config("alembic.ini"))

    with migrated_engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert revision == "0005_persistent_job_system"
    job_columns = {column["name"] for column in inspect(migrated_engine).get_columns("jobs")}
    assert {"cancel_requested", "execute_now_requested", "step"}.issubset(job_columns)


def test_sqlite_connections_enable_integrity_and_concurrency_pragmas(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert busy_timeout == 5000


def test_session_scope_commits_and_rolls_back(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)

    with session_scope(factory) as session:
        session.add(User(username="admin", password_hash="hash-ficticio"))

    with pytest.raises(IntegrityError), session_scope(factory) as session:
        session.add(User(username="admin", password_hash="outro-hash-ficticio"))

    with session_scope(factory) as session:
        users = session.scalars(select(User)).all()

    assert [user.username for user in users] == ["admin"]


def test_initial_migration_can_downgrade_to_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "downgrade.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_database_engine(database_path)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
