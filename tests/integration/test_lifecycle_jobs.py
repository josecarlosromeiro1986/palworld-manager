from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, AuditEvent, Job, User
from app.health.palworld import PalworldHealthState
from app.lifecycle.jobs import (
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    LifecycleJobConflictError,
    claim_next_lifecycle_job,
    enqueue_lifecycle_job,
    execute_lifecycle_job,
    lifecycle_timeout,
)
from app.lifecycle.service import LifecycleAction, LifecycleOutcome, LifecycleResult
from app.lifecycle.worker import LifecycleJobWorker


@pytest.fixture
def lifecycle_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


class RecordingExecutor:
    def __init__(self, result: LifecycleResult) -> None:
        self.result = result
        self.calls: list[tuple[LifecycleAction, int]] = []

    def execute(self, action: LifecycleAction, timeout_seconds: int) -> LifecycleResult:
        self.calls.append((action, timeout_seconds))
        return self.result


class FailingExecutor:
    def execute(self, action: LifecycleAction, timeout_seconds: int) -> LifecycleResult:
        del action, timeout_seconds
        raise RuntimeError("detalhe interno simulado")


def _create_user(engine: Engine) -> int:
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        user = User(username="admin", password_hash="hash-ficticio")
        session.add(user)
        session.flush()
        return user.id


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (LifecycleAction.START, 120),
        (LifecycleAction.RESTART, 120),
        (LifecycleAction.STOP, 60),
    ],
)
def test_lifecycle_timeout_defaults(
    lifecycle_engine: Engine,
    action: LifecycleAction,
    expected: int,
) -> None:
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        assert lifecycle_timeout(session, action) == expected


@pytest.mark.parametrize(
    ("action", "key", "configured"),
    [
        (LifecycleAction.START, "start_timeout_seconds", 37),
        (LifecycleAction.RESTART, "restart_timeout_seconds", 41),
        (LifecycleAction.STOP, "stop_timeout_seconds", 23),
    ],
)
def test_lifecycle_timeout_uses_configurable_database_value(
    lifecycle_engine: Engine,
    action: LifecycleAction,
    key: str,
    configured: int,
) -> None:
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        session.add(AppSetting(key=key, value=configured))
    with session_scope(factory) as session:
        assert lifecycle_timeout(session, action) == configured


def test_enqueue_persists_job_timeout_and_administrator_audit(
    lifecycle_engine: Engine,
) -> None:
    user_id = _create_user(lifecycle_engine)
    factory = create_session_factory(lifecycle_engine)

    with session_scope(factory) as session:
        job = enqueue_lifecycle_job(session, LifecycleAction.RESTART, user_id=user_id)
        job_id = job.id

    with session_scope(factory) as session:
        stored = session.get_one(Job, job_id)
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "RESTART_SERVER_REQUESTED")
        )
        assert stored.status == JOB_STATUS_PENDING
        assert stored.result == {"timeout_seconds": 120}
        assert stored.is_cancellable is False
        assert stored.requires_maintenance_lock is True
        assert stored.coordination_key == "PALWORLD_LIFECYCLE"
        assert event is not None
        assert event.user_id == user_id
        assert event.job_id == job_id


def test_second_lifecycle_job_is_rejected_while_one_is_active(
    lifecycle_engine: Engine,
) -> None:
    user_id = _create_user(lifecycle_engine)
    factory = create_session_factory(lifecycle_engine)

    with session_scope(factory) as session:
        enqueue_lifecycle_job(session, LifecycleAction.START, user_id=user_id)
    with pytest.raises(LifecycleJobConflictError), session_scope(factory) as session:
        enqueue_lifecycle_job(session, LifecycleAction.STOP, user_id=user_id)


def test_database_guard_rejects_two_active_lifecycle_coordination_keys(
    lifecycle_engine: Engine,
) -> None:
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        session.add(
            Job(
                kind="PALWORLD_START",
                status=JOB_STATUS_RUNNING,
                coordination_key="PALWORLD_LIFECYCLE",
            )
        )
    with pytest.raises(IntegrityError), session_scope(factory) as session:
        session.add(
            Job(
                kind="PALWORLD_STOP",
                status=JOB_STATUS_PENDING,
                coordination_key="PALWORLD_LIFECYCLE",
            )
        )


def test_claim_is_single_and_execution_uses_enqueued_timeout(
    lifecycle_engine: Engine,
) -> None:
    user_id = _create_user(lifecycle_engine)
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        session.add(AppSetting(key="stop_timeout_seconds", value=23))
        session.flush()
        enqueue_lifecycle_job(session, LifecycleAction.STOP, user_id=user_id)

    with session_scope(factory) as session:
        claimed = claim_next_lifecycle_job(session, "worker-test")
        assert claimed is not None
        job_id = claimed.id
        assert claimed.status == JOB_STATUS_RUNNING
    with session_scope(factory) as session:
        assert claim_next_lifecycle_job(session, "worker-2") is None

    executor = RecordingExecutor(
        LifecycleResult(
            LifecycleOutcome.SUCCEEDED,
            PalworldHealthState.OFFLINE,
            timed_out=False,
        )
    )
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        execute_lifecycle_job(session, job, executor)

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        completion = session.scalar(select(AuditEvent).where(AuditEvent.action == "STOP_SERVER"))
        assert executor.calls == [(LifecycleAction.STOP, 23)]
        assert job.status == JOB_STATUS_SUCCEEDED
        assert job.progress == 100
        assert job.result == {
            "timeout_seconds": 23,
            "timed_out": False,
            "final_state": "OFFLINE",
        }
        assert completion is not None
        assert completion.result == "SUCCESS"
        assert completion.job_id == job_id


def test_timeout_marks_job_and_audit_as_failed(lifecycle_engine: Engine) -> None:
    user_id = _create_user(lifecycle_engine)
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        enqueue_lifecycle_job(session, LifecycleAction.START, user_id=user_id)
    with session_scope(factory) as session:
        job = claim_next_lifecycle_job(session, "worker-test")
        assert job is not None
        job_id = job.id

    executor = RecordingExecutor(
        LifecycleResult(
            LifecycleOutcome.FAILED,
            PalworldHealthState.STARTING,
            timed_out=True,
        )
    )
    with session_scope(factory) as session:
        execute_lifecycle_job(session, session.get_one(Job, job_id), executor)

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        completion = session.scalar(select(AuditEvent).where(AuditEvent.action == "START_SERVER"))
        assert job.status == JOB_STATUS_FAILED
        assert job.result is not None
        assert job.result["timed_out"] is True
        assert completion is not None
        assert completion.result == "FAILURE"


def test_lifecycle_worker_claims_and_completes_one_job(lifecycle_engine: Engine) -> None:
    user_id = _create_user(lifecycle_engine)
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        enqueue_lifecycle_job(session, LifecycleAction.START, user_id=user_id)
    executor = RecordingExecutor(
        LifecycleResult(
            LifecycleOutcome.SUCCEEDED,
            PalworldHealthState.ONLINE,
            timed_out=False,
        )
    )
    worker = LifecycleJobWorker(factory, executor, worker_id="worker-test")

    assert worker.process_next() is True
    assert worker.process_next() is False

    with session_scope(factory) as session:
        job = session.scalar(select(Job))
        assert job is not None
        assert job.status == JOB_STATUS_SUCCEEDED
        assert job.claimed_by == "worker-test"


def test_lifecycle_worker_records_unexpected_failure_without_details(
    lifecycle_engine: Engine,
) -> None:
    user_id = _create_user(lifecycle_engine)
    factory = create_session_factory(lifecycle_engine)
    with session_scope(factory) as session:
        enqueue_lifecycle_job(session, LifecycleAction.RESTART, user_id=user_id)
    worker = LifecycleJobWorker(factory, FailingExecutor(), worker_id="worker-test")

    assert worker.process_next() is True

    with session_scope(factory) as session:
        job = session.scalar(select(Job))
        completion = session.scalar(select(AuditEvent).where(AuditEvent.action == "RESTART_SERVER"))
        assert job is not None
        assert job.status == JOB_STATUS_FAILED
        assert job.finished_at is not None
        assert job.result == {"unexpected_failure": True}
        assert completion is not None
        assert completion.details == {"unexpected_failure": True}
        assert "detalhe interno" not in str(completion.details)
