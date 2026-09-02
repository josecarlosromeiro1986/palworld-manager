from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_CANCELLED,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.db.engine import session_scope
from app.db.models import AppSetting, Job
from app.health.palworld import PalworldHealthState
from app.jobs.service import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STEP_CANCELLED,
    JOB_STEP_COMPLETED,
    JOB_STEP_COUNTDOWN,
    JOB_STEP_FAILED,
    JOB_STEP_IRREVERSIBLE,
    JOB_STEP_WAITING,
    claim_next_job,
)
from app.lifecycle.jobs import (
    LIFECYCLE_COORDINATION_KEY,
    lifecycle_timeout,
)
from app.lifecycle.service import LifecycleAction
from app.notifications.service import FORCED_SHUTDOWN, enqueue_discord_notification
from app.shutdown.service import (
    AssistedShutdownExecutor,
    AssistedShutdownResult,
    CountdownDirective,
    ForcedShutdownExecutor,
    ShutdownOutcome,
)
from app.system.palworld_service import PalworldSignal

ASSISTED_SHUTDOWN_DEFAULT_KEY = "assisted_shutdown_default_minutes"
ALLOWED_COUNTDOWN_MINUTES = (0, 1, 5, 10)


class ShutdownJobKind(StrEnum):
    ASSISTED = "PALWORLD_ASSISTED_SHUTDOWN"
    FORCE_TERM = "PALWORLD_FORCE_SIGTERM"
    FORCE_KILL = "PALWORLD_FORCE_SIGKILL"


class ShutdownJobConflictError(RuntimeError):
    """Já existe uma ação do servidor pendente ou em execução."""


class InvalidForcedShutdownError(RuntimeError):
    """A escalada solicitada não possui uma falha anterior válida."""


@dataclass(frozen=True, slots=True)
class ShutdownJobView:
    id: int
    kind: ShutdownJobKind
    status: str
    progress: int
    step: str
    is_cancellable: bool
    remaining_seconds: int | None
    timed_out: bool | None
    final_state: str | None
    failure: str | None


def assisted_shutdown_default(session: Session) -> int:
    setting = session.get(AppSetting, ASSISTED_SHUTDOWN_DEFAULT_KEY)
    if setting is None:
        return 5
    value = setting.value
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value not in ALLOWED_COUNTDOWN_MINUTES
    ):
        raise ValueError("assisted_shutdown_default_minutes possui valor inválido")
    return value


def enqueue_assisted_shutdown(
    session: Session,
    countdown_minutes: int,
    *,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    if countdown_minutes not in ALLOWED_COUNTDOWN_MINUTES:
        raise ValueError("duração de desligamento assistido inválida")
    _ensure_no_active_palworld_job(session)
    timeout_seconds = lifecycle_timeout(session, LifecycleAction.STOP)
    job = Job(
        kind=ShutdownJobKind.ASSISTED.value,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=countdown_minutes > 0,
        requires_maintenance_lock=True,
        coordination_key=LIFECYCLE_COORDINATION_KEY,
        result={
            "countdown_minutes": countdown_minutes,
            "remaining_seconds": countdown_minutes * 60,
            "stop_timeout_seconds": timeout_seconds,
        },
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise ShutdownJobConflictError("Já existe uma ação do servidor em andamento.") from error
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action="ASSISTED_SHUTDOWN_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target="Palworld",
        details={"countdown_minutes": countdown_minutes},
    )
    return job


def enqueue_forced_shutdown(
    session: Session,
    source_job_id: int,
    signal: PalworldSignal,
    *,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    source = session.get(Job, source_job_id)
    allowed_sources = (
        {"PALWORLD_STOP", ShutdownJobKind.ASSISTED.value}
        if signal is PalworldSignal.TERM
        else {ShutdownJobKind.FORCE_TERM.value}
    )
    source_failure = (source.result or {}).get("failure") if source is not None else None
    assisted_stop_failed = (
        source is not None
        and source.kind == ShutdownJobKind.ASSISTED.value
        and source_failure == "stop_failed"
    )
    valid_term_source = (
        source is not None
        and signal is PalworldSignal.TERM
        and (source.kind == "PALWORLD_STOP" or assisted_stop_failed)
    )
    valid_kill_source = (
        source is not None
        and signal is PalworldSignal.KILL
        and source.kind == ShutdownJobKind.FORCE_TERM.value
    )
    if (
        source is None
        or source.status != JOB_STATUS_FAILED
        or source.kind not in allowed_sources
        or not (valid_term_source or valid_kill_source)
    ):
        raise InvalidForcedShutdownError("A etapa forçada exige uma falha anterior válida.")
    _ensure_no_active_palworld_job(session)
    job_kind = (
        ShutdownJobKind.FORCE_TERM if signal is PalworldSignal.TERM else ShutdownJobKind.FORCE_KILL
    )
    timeout_seconds = lifecycle_timeout(session, LifecycleAction.STOP)
    job = Job(
        kind=job_kind.value,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=False,
        requires_maintenance_lock=True,
        coordination_key=LIFECYCLE_COORDINATION_KEY,
        result={"source_job_id": source_job_id, "timeout_seconds": timeout_seconds},
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise ShutdownJobConflictError("Já existe uma ação do servidor em andamento.") from error
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action=f"FORCED_SHUTDOWN_{signal.value}_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target="Palworld",
        details={"source_job_id": source_job_id, "signal": signal.value},
    )
    return job


def request_shutdown_cancel(session: Session, job_id: int, *, user_id: int) -> bool:
    changed_status = session.scalar(
        update(Job)
        .where(
            Job.id == job_id,
            Job.kind == ShutdownJobKind.ASSISTED.value,
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.is_cancellable.is_(True),
            Job.cancel_requested.is_(False),
        )
        .values(cancel_requested=True)
        .returning(Job.status)
    )
    if changed_status is None:
        return False
    job = session.get_one(Job, job_id)
    requested_at = datetime.now(UTC)
    record_audit_event(
        session,
        occurred_at=requested_at,
        action="ASSISTED_SHUTDOWN_CANCEL_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job_id,
        target="Palworld",
    )
    if changed_status == JOB_STATUS_PENDING:
        job.status = JOB_STATUS_CANCELLED
        job.step = JOB_STEP_CANCELLED
        job.progress = 100
        job.is_cancellable = False
        job.finished_at = requested_at
        result = dict(job.result or {})
        result["cancelled"] = True
        job.result = result
        record_audit_event(
            session,
            occurred_at=job.finished_at,
            action="ASSISTED_SHUTDOWN",
            result=AUDIT_RESULT_CANCELLED,
            origin=AUDIT_ORIGIN_SYSTEM,
            job_id=job.id,
            target="Palworld",
        )
    return True


def request_shutdown_now(session: Session, job_id: int, *, user_id: int) -> bool:
    changed_id = session.scalar(
        update(Job)
        .where(
            Job.id == job_id,
            Job.kind == ShutdownJobKind.ASSISTED.value,
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.is_cancellable.is_(True),
            Job.cancel_requested.is_(False),
            Job.execute_now_requested.is_(False),
        )
        .values(execute_now_requested=True)
        .returning(Job.id)
    )
    if changed_id is None:
        return False
    record_audit_event(
        session,
        occurred_at=datetime.now(UTC),
        action="ASSISTED_SHUTDOWN_EXECUTE_NOW_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job_id,
        target="Palworld",
    )
    return True


def claim_next_shutdown_job(
    session: Session, worker_id: str, *, claimed_at: datetime | None = None
) -> Job | None:
    return claim_next_job(
        session,
        worker_id,
        tuple(kind.value for kind in ShutdownJobKind),
        claimed_at=claimed_at,
    )


class DatabaseCountdownControl:
    def __init__(self, session_factory: sessionmaker[Session], job_id: int) -> None:
        self._session_factory = session_factory
        self._job_id = job_id

    def update(self, remaining_seconds: int, total_seconds: int) -> CountdownDirective:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, self._job_id)
            job.step = JOB_STEP_COUNTDOWN
            result = dict(job.result or {})
            result["remaining_seconds"] = remaining_seconds
            job.result = result
            job.progress = 5 + int(75 * (total_seconds - remaining_seconds) / total_seconds)
            if job.cancel_requested:
                return CountdownDirective.CANCEL
            if job.execute_now_requested:
                return CountdownDirective.EXECUTE_NOW
            return CountdownDirective.CONTINUE

    def mark_irreversible(self) -> CountdownDirective:
        with session_scope(self._session_factory) as session:
            changed_id = session.scalar(
                update(Job)
                .where(
                    Job.id == self._job_id,
                    Job.is_cancellable.is_(True),
                    Job.cancel_requested.is_(False),
                )
                .values(is_cancellable=False, progress=80)
                .returning(Job.id)
            )
            job = session.get_one(Job, self._job_id)
            if changed_id is None and job.cancel_requested:
                return CountdownDirective.CANCEL
            job.is_cancellable = False
            job.step = JOB_STEP_IRREVERSIBLE
            job.progress = 80
            result = dict(job.result or {})
            result["remaining_seconds"] = 0
            job.result = result
            return CountdownDirective.CONTINUE


def execute_assisted_shutdown_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    executor: AssistedShutdownExecutor,
) -> None:
    with session_scope(session_factory) as session:
        job = session.get_one(Job, job_id)
        result_data = job.result or {}
        minutes = _required_int(result_data, "countdown_minutes", minimum=0, maximum=10)
        timeout = _required_int(result_data, "stop_timeout_seconds", minimum=1, maximum=300)
    result = executor.execute(minutes, timeout, DatabaseCountdownControl(session_factory, job_id))
    _complete_shutdown_job(session_factory, job_id, result, "ASSISTED_SHUTDOWN")


def execute_forced_shutdown_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    executor: ForcedShutdownExecutor,
) -> None:
    with session_scope(session_factory) as session:
        job = session.get_one(Job, job_id)
        kind = ShutdownJobKind(job.kind)
        timeout = _required_int(job.result or {}, "timeout_seconds", minimum=1, maximum=300)
    signal = PalworldSignal.TERM if kind is ShutdownJobKind.FORCE_TERM else PalworldSignal.KILL
    result = executor.execute(signal, timeout)
    _complete_shutdown_job(
        session_factory,
        job_id,
        result,
        f"FORCED_SHUTDOWN_{signal.value}",
        notify_forced_shutdown=True,
    )


def fail_shutdown_job(session_factory: sessionmaker[Session], job_id: int) -> None:
    result = AssistedShutdownResult(
        ShutdownOutcome.FAILED, None, False, PalworldHealthState.FAILURE, "unexpected_failure"
    )
    _complete_shutdown_job(session_factory, job_id, result, "SHUTDOWN")


def _complete_shutdown_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    result: AssistedShutdownResult,
    audit_action: str,
    *,
    notify_forced_shutdown: bool = False,
) -> None:
    with session_scope(session_factory) as session:
        job = session.get_one(Job, job_id)
        job.status = result.outcome.value
        job.step = {
            ShutdownOutcome.SUCCEEDED: JOB_STEP_COMPLETED,
            ShutdownOutcome.FAILED: JOB_STEP_FAILED,
            ShutdownOutcome.CANCELLED: JOB_STEP_CANCELLED,
        }[result.outcome]
        job.progress = 100
        job.is_cancellable = False
        job.finished_at = datetime.now(UTC)
        previous = dict(job.result or {})
        previous.update(
            {
                "online_players": result.online_players,
                "timed_out": result.timed_out,
                "final_state": result.final_state.value if result.final_state else None,
                "failure": result.failure,
            }
        )
        job.result = previous
        audit_result = {
            ShutdownOutcome.SUCCEEDED: AUDIT_RESULT_SUCCESS,
            ShutdownOutcome.FAILED: AUDIT_RESULT_FAILURE,
            ShutdownOutcome.CANCELLED: AUDIT_RESULT_CANCELLED,
        }[result.outcome]
        record_audit_event(
            session,
            occurred_at=job.finished_at,
            action=audit_action,
            result=audit_result,
            origin=AUDIT_ORIGIN_SYSTEM,
            job_id=job.id,
            target="Palworld",
            details={
                "timed_out": result.timed_out,
                "final_state": result.final_state.value if result.final_state else None,
                "failure": result.failure,
            },
        )
        if notify_forced_shutdown:
            enqueue_discord_notification(session, FORCED_SHUTDOWN, job_id=job_id)


def shutdown_job_view(job: Job) -> ShutdownJobView:
    kind = ShutdownJobKind(job.kind)
    result = job.result or {}
    remaining = result.get("remaining_seconds")
    timed_out = result.get("timed_out")
    final_state = result.get("final_state")
    failure = result.get("failure")
    return ShutdownJobView(
        id=job.id,
        kind=kind,
        status=job.status,
        progress=job.progress,
        step=job.step,
        is_cancellable=job.is_cancellable and job.status in ACTIVE_JOB_STATUSES,
        remaining_seconds=remaining if isinstance(remaining, int) else None,
        timed_out=timed_out if isinstance(timed_out, bool) else None,
        final_state=final_state if isinstance(final_state, str) else None,
        failure=failure if isinstance(failure, str) else None,
    )


def _ensure_no_active_palworld_job(session: Session) -> None:
    if (
        session.scalar(
            select(Job.id).where(
                Job.coordination_key == LIFECYCLE_COORDINATION_KEY,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        is not None
    ):
        raise ShutdownJobConflictError("Já existe uma ação do servidor em andamento.")


def _required_int(data: dict[str, object], key: str, *, minimum: int, maximum: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"job possui {key} inválido")
    return value
