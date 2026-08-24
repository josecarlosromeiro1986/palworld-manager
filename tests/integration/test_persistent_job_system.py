from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select

from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, Job, MaintenanceLock, WorkerHeartbeat
from app.jobs.heartbeat import (
    WORKER_HEARTBEAT_KEY,
    WorkerAlreadyRunningError,
    record_worker_heartbeat,
    record_worker_start,
)
from app.jobs.service import (
    GLOBAL_MAINTENANCE_LOCK,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_RUNNING,
    claim_next_job,
    recover_interrupted_jobs,
    release_maintenance_lock,
)


@pytest.fixture
def jobs_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def test_claim_is_atomic_and_same_job_is_not_returned_twice(jobs_engine: Engine) -> None:
    factory = create_session_factory(jobs_engine)
    with session_scope(factory) as session:
        session.add(Job(kind="TEST_JOB", status="PENDING"))

    with session_scope(factory) as session:
        first = claim_next_job(session, "worker-one", ("TEST_JOB",))
        assert first is not None
        first_id = first.id
    with session_scope(factory) as session:
        second = claim_next_job(session, "worker-two", ("TEST_JOB",))

    assert second is None
    with session_scope(factory) as session:
        stored = session.get_one(Job, first_id)
        assert stored.status == JOB_STATUS_RUNNING
        assert stored.claimed_by == "worker-one"


def test_concurrent_workers_cannot_claim_the_same_job(jobs_engine: Engine) -> None:
    factory = create_session_factory(jobs_engine)
    with session_scope(factory) as session:
        session.add(Job(kind="CONCURRENT_JOB", status="PENDING"))

    def claim(worker_id: str) -> int | None:
        with session_scope(factory) as session:
            job = claim_next_job(session, worker_id, ("CONCURRENT_JOB",))
            return job.id if job is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ("worker-one", "worker-two")))

    assert sum(job_id is not None for job_id in claimed) == 1


def test_global_lock_serializes_incompatible_jobs(jobs_engine: Engine) -> None:
    factory = create_session_factory(jobs_engine)
    with session_scope(factory) as session:
        session.add_all(
            [
                Job(kind="LOCKED_A", status="PENDING", requires_maintenance_lock=True),
                Job(kind="LOCKED_B", status="PENDING", requires_maintenance_lock=True),
            ]
        )

    with session_scope(factory) as session:
        first = claim_next_job(session, "worker-one", ("LOCKED_A", "LOCKED_B"))
        assert first is not None
        first_id = first.id
    with session_scope(factory) as session:
        assert claim_next_job(session, "worker-two", ("LOCKED_A", "LOCKED_B")) is None
        lock = session.get_one(MaintenanceLock, GLOBAL_MAINTENANCE_LOCK)
        assert lock.job_id == first_id

    with session_scope(factory) as session:
        session.get_one(Job, first_id).status = "SUCCEEDED"
        release_maintenance_lock(session, first_id)
    with session_scope(factory) as session:
        second = claim_next_job(session, "worker-two", ("LOCKED_A", "LOCKED_B"))
        assert second is not None
        assert second.id != first_id


def test_worker_recovery_interrupts_running_jobs_without_requeue(jobs_engine: Engine) -> None:
    factory = create_session_factory(jobs_engine)
    with session_scope(factory) as session:
        job = Job(
            kind="PALWORLD_RESTART",
            status=JOB_STATUS_RUNNING,
            step="IRREVERSIBLE",
            requires_maintenance_lock=True,
            coordination_key="PALWORLD_LIFECYCLE",
            claimed_by="old-worker",
        )
        session.add(job)
        session.flush()
        job_id = job.id
        session.add(
            MaintenanceLock(
                key=GLOBAL_MAINTENANCE_LOCK,
                job_id=job_id,
                worker_id="old-worker",
                acquired_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            )
        )

    with session_scope(factory) as session:
        recovered = recover_interrupted_jobs(
            session,
            recovered_at=datetime(2026, 8, 14, 12, 1, tzinfo=UTC),
        )

    assert [item.id for item in recovered] == [job_id]
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        audit = session.scalar(select(AuditEvent).where(AuditEvent.job_id == job_id))
        assert job.status == JOB_STATUS_INTERRUPTED
        assert job.step == "INTERRUPTED"
        assert job.finished_at is not None
        assert job.result == {"interrupted": True, "requires_manual_review": True}
        assert session.get(MaintenanceLock, GLOBAL_MAINTENANCE_LOCK) is None
        assert audit is not None
        assert audit.action == "JOB_INTERRUPTED"
        assert audit.result == "INTERRUPTED"
    with session_scope(factory) as session:
        assert claim_next_job(session, "new-worker", ("PALWORLD_RESTART",)) is None


def test_worker_heartbeat_start_and_refresh_are_persistent(jobs_engine: Engine) -> None:
    factory = create_session_factory(jobs_engine)
    started_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    refreshed_at = started_at + timedelta(seconds=10)
    with session_scope(factory) as session:
        record_worker_start(session, "worker-one", started_at=started_at)
    with session_scope(factory) as session:
        assert record_worker_heartbeat(
            session,
            "worker-one",
            heartbeat_at=refreshed_at,
        )

    with session_scope(factory) as session:
        heartbeat = session.get_one(WorkerHeartbeat, WORKER_HEARTBEAT_KEY)
        assert heartbeat.worker_id == "worker-one"
        assert heartbeat.heartbeat_at == refreshed_at.replace(tzinfo=None)


def test_recent_heartbeat_prevents_second_worker_identity(jobs_engine: Engine) -> None:
    factory = create_session_factory(jobs_engine)
    started_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    with session_scope(factory) as session:
        record_worker_start(session, "worker-one", started_at=started_at)

    with pytest.raises(WorkerAlreadyRunningError), session_scope(factory) as session:
        record_worker_start(
            session,
            "worker-two",
            started_at=started_at + timedelta(seconds=29),
        )

    with session_scope(factory) as session:
        record_worker_start(
            session,
            "worker-two",
            started_at=started_at + timedelta(seconds=30),
        )
    with session_scope(factory) as session:
        heartbeat = session.get_one(WorkerHeartbeat, WORKER_HEARTBEAT_KEY)
        assert heartbeat.worker_id == "worker-two"
