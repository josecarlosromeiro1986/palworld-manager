from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Select, delete, exists, insert, or_, select, update
from sqlalchemy.orm import Session

from app.audit.service import (
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_INTERRUPTED,
    record_audit_event,
)
from app.db.models import Job, MaintenanceLock

JOB_STATUS_PENDING: Final = "PENDING"
JOB_STATUS_RUNNING: Final = "RUNNING"
JOB_STATUS_SUCCEEDED: Final = "SUCCEEDED"
JOB_STATUS_FAILED: Final = "FAILED"
JOB_STATUS_CANCELLED: Final = "CANCELLED"
JOB_STATUS_INTERRUPTED: Final = "INTERRUPTED"

ACTIVE_JOB_STATUSES: Final = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING)
TERMINAL_JOB_STATUSES: Final = (
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_INTERRUPTED,
)

JOB_STEP_WAITING: Final = "WAITING"
JOB_STEP_EXECUTING: Final = "EXECUTING"
JOB_STEP_COUNTDOWN: Final = "COUNTDOWN"
JOB_STEP_IRREVERSIBLE: Final = "IRREVERSIBLE"
JOB_STEP_COMPLETED: Final = "COMPLETED"
JOB_STEP_FAILED: Final = "FAILED"
JOB_STEP_CANCELLED: Final = "CANCELLED"
JOB_STEP_INTERRUPTED: Final = "INTERRUPTED"

GLOBAL_MAINTENANCE_LOCK: Final = "GLOBAL"


@dataclass(frozen=True, slots=True)
class InterruptedJob:
    id: int
    kind: str
    log_path: str | None


def _pending_supported_job(supported_kinds: tuple[str, ...]) -> Select[tuple[int]]:
    lock_is_free = ~exists().where(MaintenanceLock.key == GLOBAL_MAINTENANCE_LOCK)
    return (
        select(Job.id)
        .where(
            Job.kind.in_(supported_kinds),
            Job.status == JOB_STATUS_PENDING,
            or_(Job.requires_maintenance_lock.is_(False), lock_is_free),
        )
        .order_by(Job.created_at, Job.id)
        .limit(1)
    )


def claim_next_job(
    session: Session,
    worker_id: str,
    supported_kinds: tuple[str, ...],
    *,
    claimed_at: datetime | None = None,
) -> Job | None:
    if not worker_id:
        raise ValueError("o identificador do worker é obrigatório")
    if not supported_kinds:
        return None

    _release_terminal_locks(session)
    now = claimed_at or datetime.now(UTC)
    statement = (
        update(Job)
        .where(
            Job.id == _pending_supported_job(supported_kinds).scalar_subquery(),
            Job.status == JOB_STATUS_PENDING,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            step=JOB_STEP_EXECUTING,
            claimed_by=worker_id,
            claimed_at=now,
            started_at=now,
            progress=5,
        )
        .returning(Job)
    )
    job = session.scalars(statement).one_or_none()
    if job is None or not job.requires_maintenance_lock:
        return job

    acquired = session.scalar(
        insert(MaintenanceLock)
        .prefix_with("OR IGNORE")
        .values(
            key=GLOBAL_MAINTENANCE_LOCK,
            job_id=job.id,
            worker_id=worker_id,
            acquired_at=now,
        )
        .returning(MaintenanceLock.job_id)
    )
    if acquired is not None:
        return job

    job.status = JOB_STATUS_PENDING
    job.step = JOB_STEP_WAITING
    job.claimed_by = None
    job.claimed_at = None
    job.started_at = None
    job.progress = 0
    session.flush()
    return None


def release_maintenance_lock(session: Session, job_id: int) -> None:
    session.execute(delete(MaintenanceLock).where(MaintenanceLock.job_id == job_id))


def recover_interrupted_jobs(
    session: Session,
    *,
    recovered_at: datetime | None = None,
) -> tuple[InterruptedJob, ...]:
    now = recovered_at or datetime.now(UTC)
    running_jobs = tuple(
        session.scalars(select(Job).where(Job.status == JOB_STATUS_RUNNING).order_by(Job.id))
    )
    interrupted: list[InterruptedJob] = []
    for job in running_jobs:
        job.status = JOB_STATUS_INTERRUPTED
        job.step = JOB_STEP_INTERRUPTED
        job.is_cancellable = False
        job.finished_at = now
        result = dict(job.result or {})
        result.update({"interrupted": True, "requires_manual_review": True})
        job.result = result
        interrupted.append(InterruptedJob(job.id, job.kind, job.log_path))
        record_audit_event(
            session,
            occurred_at=now,
            action="JOB_INTERRUPTED",
            result=AUDIT_RESULT_INTERRUPTED,
            origin=AUDIT_ORIGIN_SYSTEM,
            job_id=job.id,
            target=job.kind,
            details={"requires_manual_review": True},
        )
    if interrupted:
        session.execute(
            delete(MaintenanceLock).where(
                MaintenanceLock.job_id.in_([job.id for job in interrupted])
            )
        )
    _release_terminal_locks(session)
    return tuple(interrupted)


def _release_terminal_locks(session: Session) -> None:
    session.execute(
        delete(MaintenanceLock).where(
            MaintenanceLock.job_id.in_(select(Job.id).where(Job.status.in_(TERMINAL_JOB_STATUSES)))
        )
    )
