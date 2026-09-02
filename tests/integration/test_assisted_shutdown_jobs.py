from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select

from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, Job, NotificationEvent
from app.jobs.service import JOB_STATUS_CANCELLED, JOB_STATUS_FAILED
from app.lifecycle.service import create_lifecycle_executor
from app.lifecycle.worker import LifecycleJobWorker
from app.shutdown.jobs import (
    InvalidForcedShutdownError,
    ShutdownJobKind,
    enqueue_assisted_shutdown,
    enqueue_forced_shutdown,
    request_shutdown_cancel,
)
from app.shutdown.service import create_shutdown_executors
from app.system.palworld_service import PalworldSignal


@pytest.fixture
def shutdown_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def _user_id(engine: Engine) -> int:
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        user = create_administrator(session, "admin", "senha-ficticia")
        return user.id


def test_pending_countdown_can_be_cancelled_and_is_audited(shutdown_engine: Engine) -> None:
    factory = create_session_factory(shutdown_engine)
    user_id = _user_id(shutdown_engine)
    with session_scope(factory) as session:
        job = enqueue_assisted_shutdown(session, 5, user_id=user_id)
        job_id = job.id
    with session_scope(factory) as session:
        assert request_shutdown_cancel(session, job_id, user_id=user_id) is True

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "ASSISTED_SHUTDOWN",
                AuditEvent.result == "CANCELLED",
            )
        )
        assert job.status == JOB_STATUS_CANCELLED
        assert job.cancel_requested is True
        assert job.is_cancellable is False
        assert audit is not None
    with session_scope(factory) as session:
        assert request_shutdown_cancel(session, job_id, user_id=user_id) is False


def test_worker_executes_now_through_normal_stop_with_shared_fake(
    shutdown_engine: Engine,
) -> None:
    factory = create_session_factory(shutdown_engine)
    user_id = _user_id(shutdown_engine)
    with session_scope(factory) as session:
        job = enqueue_assisted_shutdown(session, 1, user_id=user_id)
        job.execute_now_requested = True
        job_id = job.id

    database_path = shutdown_engine.url.database
    assert database_path is not None
    settings = Settings(environment=AppEnvironment.TEST, manager_database=Path(database_path))
    assisted, forced = create_shutdown_executors(settings, factory)
    worker = LifecycleJobWorker(
        factory,
        create_lifecycle_executor(settings, factory),
        worker_id="worker-test",
        assisted_shutdown_executor=assisted,
        forced_shutdown_executor=forced,
    )

    assert worker.process_next() is True
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "SUCCEEDED"
        assert job.result is not None
        assert job.result["remaining_seconds"] == 0
        assert job.result["final_state"] == "OFFLINE"


def test_sigkill_requires_a_failed_sigterm_and_creates_no_automatic_job(
    shutdown_engine: Engine,
) -> None:
    factory = create_session_factory(shutdown_engine)
    user_id = _user_id(shutdown_engine)
    with session_scope(factory) as session:
        assisted = enqueue_assisted_shutdown(session, 0, user_id=user_id)
        assisted.status = JOB_STATUS_FAILED
        assisted.finished_at = assisted.created_at
        assisted.result = {**(assisted.result or {}), "failure": "stop_failed"}
        source_id = assisted.id
    with pytest.raises(InvalidForcedShutdownError), session_scope(factory) as session:
        enqueue_forced_shutdown(session, source_id, PalworldSignal.KILL, user_id=user_id)

    with session_scope(factory) as session:
        term = enqueue_forced_shutdown(session, source_id, PalworldSignal.TERM, user_id=user_id)
        term_id = term.id
        term.status = JOB_STATUS_FAILED
        term.finished_at = term.created_at
    with session_scope(factory) as session:
        kill = enqueue_forced_shutdown(session, term_id, PalworldSignal.KILL, user_id=user_id)
        assert kill.kind == ShutdownJobKind.FORCE_KILL.value
        assert kill.status == "PENDING"

    with session_scope(factory) as session:
        kinds = list(session.scalars(select(Job.kind).order_by(Job.id)))
        notifications = list(session.scalars(select(NotificationEvent)))
        assert kinds == [
            ShutdownJobKind.ASSISTED.value,
            ShutdownJobKind.FORCE_TERM.value,
            ShutdownJobKind.FORCE_KILL.value,
        ]
        assert notifications == []


def test_worker_executes_explicit_sigterm_and_queues_discord_alert(
    shutdown_engine: Engine,
) -> None:
    factory = create_session_factory(shutdown_engine)
    user_id = _user_id(shutdown_engine)
    with session_scope(factory) as session:
        source = enqueue_assisted_shutdown(session, 0, user_id=user_id)
        source.status = JOB_STATUS_FAILED
        source.result = {**(source.result or {}), "failure": "stop_failed"}
        source_id = source.id
    with session_scope(factory) as session:
        forced = enqueue_forced_shutdown(session, source_id, PalworldSignal.TERM, user_id=user_id)
        forced_id = forced.id

    database_path = shutdown_engine.url.database
    assert database_path is not None
    settings = Settings(environment=AppEnvironment.TEST, manager_database=Path(database_path))
    assisted_executor, forced_executor = create_shutdown_executors(settings, factory)
    worker = LifecycleJobWorker(
        factory,
        create_lifecycle_executor(settings, factory),
        worker_id="worker-test",
        assisted_shutdown_executor=assisted_executor,
        forced_shutdown_executor=forced_executor,
    )
    assert worker.process_next() is True

    with session_scope(factory) as session:
        job = session.get_one(Job, forced_id)
        notification = session.scalar(select(NotificationEvent))
        assert job.status == "SUCCEEDED"
        assert notification is not None
        assert notification.event_type == "FORCED_SHUTDOWN"
        assert notification.status == "PENDING"
        assert notification.job_id == forced_id
