from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.db.models import AppSetting, Job
from app.lifecycle.service import (
    LifecycleAction,
    LifecycleExecutor,
    LifecycleOutcome,
)

JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_SUCCEEDED = "SUCCEEDED"
JOB_STATUS_FAILED = "FAILED"
ACTIVE_JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING)
LIFECYCLE_JOB_PREFIX = "PALWORLD_"
LIFECYCLE_COORDINATION_KEY = "PALWORLD_LIFECYCLE"

START_TIMEOUT_KEY = "start_timeout_seconds"
RESTART_TIMEOUT_KEY = "restart_timeout_seconds"
STOP_TIMEOUT_KEY = "stop_timeout_seconds"
DEFAULT_TIMEOUTS = {
    LifecycleAction.START: 120,
    LifecycleAction.RESTART: 120,
    LifecycleAction.STOP: 60,
}
TIMEOUT_KEYS = {
    LifecycleAction.START: START_TIMEOUT_KEY,
    LifecycleAction.RESTART: RESTART_TIMEOUT_KEY,
    LifecycleAction.STOP: STOP_TIMEOUT_KEY,
}


class LifecycleJobConflictError(RuntimeError):
    """Já existe uma ação de ciclo de vida pendente ou em execução."""


@dataclass(frozen=True, slots=True)
class LifecycleJobView:
    id: int
    action: LifecycleAction
    status: str
    progress: int
    timed_out: bool | None
    final_state: str | None


def lifecycle_job_kind(action: LifecycleAction) -> str:
    return f"{LIFECYCLE_JOB_PREFIX}{action.value}"


def active_palworld_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job)
        .where(
            Job.coordination_key == LIFECYCLE_COORDINATION_KEY,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(Job.id.desc())
        .limit(1)
    )


def parse_lifecycle_job_kind(kind: str) -> LifecycleAction:
    if not kind.startswith(LIFECYCLE_JOB_PREFIX):
        raise ValueError("tipo de job não pertence ao ciclo de vida")
    return LifecycleAction(kind.removeprefix(LIFECYCLE_JOB_PREFIX))


def lifecycle_timeout(session: Session, action: LifecycleAction) -> int:
    setting = session.get(AppSetting, TIMEOUT_KEYS[action])
    if setting is None:
        return DEFAULT_TIMEOUTS[action]
    value = setting.value
    maximum = 300 if action is LifecycleAction.STOP else 600
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{TIMEOUT_KEYS[action]} possui valor inválido")
    return value


def enqueue_lifecycle_job(
    session: Session,
    action: LifecycleAction,
    *,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    active = session.scalar(
        select(Job.id).where(
            Job.kind.like(f"{LIFECYCLE_JOB_PREFIX}%"),
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    if active is not None:
        raise LifecycleJobConflictError("Já existe uma ação do servidor em andamento.")

    timeout_seconds = lifecycle_timeout(session, action)
    job = Job(
        kind=lifecycle_job_kind(action),
        status=JOB_STATUS_PENDING,
        progress=0,
        is_cancellable=False,
        requires_maintenance_lock=True,
        coordination_key=LIFECYCLE_COORDINATION_KEY,
        result={"timeout_seconds": timeout_seconds},
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise LifecycleJobConflictError("Já existe uma ação do servidor em andamento.") from error
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action=f"{action.value}_SERVER_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target="Palworld",
        details={"timeout_seconds": timeout_seconds},
    )
    return job


def _pending_lifecycle_job() -> Select[tuple[int]]:
    kinds = [lifecycle_job_kind(action) for action in LifecycleAction]
    return (
        select(Job.id)
        .where(
            Job.kind.in_(kinds),
            Job.status == JOB_STATUS_PENDING,
        )
        .order_by(Job.created_at, Job.id)
        .limit(1)
    )


def claim_next_lifecycle_job(
    session: Session,
    worker_id: str,
    *,
    claimed_at: datetime | None = None,
) -> Job | None:
    now = claimed_at or datetime.now(UTC)
    statement = (
        update(Job)
        .where(
            Job.id == _pending_lifecycle_job().scalar_subquery(),
            Job.status == JOB_STATUS_PENDING,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            claimed_by=worker_id,
            claimed_at=now,
            started_at=now,
            progress=5,
        )
        .returning(Job)
    )
    return session.scalars(statement).one_or_none()


def execute_lifecycle_job(
    session: Session,
    job: Job,
    executor: LifecycleExecutor,
    *,
    finished_at: datetime | None = None,
) -> None:
    if job.status != JOB_STATUS_RUNNING:
        raise ValueError("somente jobs em execução podem ser concluídos")
    action = parse_lifecycle_job_kind(job.kind)
    timeout_seconds = _job_timeout(job, action)
    result = executor.execute(action, timeout_seconds)
    job.status = (
        JOB_STATUS_SUCCEEDED if result.outcome is LifecycleOutcome.SUCCEEDED else JOB_STATUS_FAILED
    )
    job.progress = 100
    job.finished_at = finished_at or datetime.now(UTC)
    job.result = {
        "timeout_seconds": timeout_seconds,
        "timed_out": result.timed_out,
        "final_state": result.final_state.value,
    }
    record_audit_event(
        session,
        occurred_at=job.finished_at,
        action=f"{action.value}_SERVER",
        result=(
            AUDIT_RESULT_SUCCESS
            if result.outcome is LifecycleOutcome.SUCCEEDED
            else AUDIT_RESULT_FAILURE
        ),
        origin=AUDIT_ORIGIN_SYSTEM,
        job_id=job.id,
        target="Palworld",
        details={
            "timed_out": result.timed_out,
            "final_state": result.final_state.value,
        },
    )


def fail_lifecycle_job(
    session: Session,
    job: Job,
    *,
    finished_at: datetime | None = None,
) -> None:
    action = parse_lifecycle_job_kind(job.kind)
    completed_at = finished_at or datetime.now(UTC)
    job.status = JOB_STATUS_FAILED
    job.progress = 100
    job.finished_at = completed_at
    job.result = {"unexpected_failure": True}
    record_audit_event(
        session,
        occurred_at=completed_at,
        action=f"{action.value}_SERVER",
        result=AUDIT_RESULT_FAILURE,
        origin=AUDIT_ORIGIN_SYSTEM,
        job_id=job.id,
        target="Palworld",
        details={"unexpected_failure": True},
    )


def _job_timeout(job: Job, action: LifecycleAction) -> int:
    result = job.result or {}
    value = result.get("timeout_seconds")
    maximum = 300 if action is LifecycleAction.STOP else 600
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError("job possui timeout inválido")
    return value


def lifecycle_job_view(job: Job) -> LifecycleJobView:
    result = job.result or {}
    timed_out = result.get("timed_out")
    final_state = result.get("final_state")
    return LifecycleJobView(
        id=job.id,
        action=parse_lifecycle_job_kind(job.kind),
        status=job.status,
        progress=job.progress,
        timed_out=timed_out if isinstance(timed_out, bool) else None,
        final_state=final_state if isinstance(final_state, str) else None,
    )
