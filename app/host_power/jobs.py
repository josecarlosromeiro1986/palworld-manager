from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.config import AppEnvironment, Settings
from app.db.engine import session_scope
from app.db.models import Job
from app.health.palworld import (
    PalworldHealthChecker,
    PalworldHealthState,
    create_palworld_health_check,
)
from app.jobs.service import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STEP_COMPLETED,
    JOB_STEP_FAILED,
    JOB_STEP_WAITING,
)
from app.lifecycle.fake import PersistentFakePalworldEnvironment
from app.lifecycle.jobs import lifecycle_timeout
from app.lifecycle.service import LifecycleAction
from app.shutdown.service import (
    AssistedShutdownResult,
    CountdownControl,
    CountdownDirective,
    ShutdownOutcome,
)
from app.system.host_power import (
    HostPowerAction,
    HostPowerControlError,
    HostPowerController,
    create_host_power_controller,
)
from app.system.palworld_service import create_palworld_service

HOST_POWER_COORDINATION_KEY: Final = "HOST_POWER"
HOST_POWER_JOB_KINDS: Final = tuple(f"HOST_{action.value}" for action in HostPowerAction)
HOST_POWER_CONFIRMATIONS: Final = {
    HostPowerAction.REBOOT: "REINICIAR UBUNTU",
    HostPowerAction.SHUTDOWN: "DESLIGAR UBUNTU",
}
HOST_POWER_STEP_CHECKING_PALWORLD: Final = "CHECKING_PALWORLD"
HOST_POWER_STEP_STOPPING_PALWORLD: Final = "STOPPING_PALWORLD"
HOST_POWER_STEP_REQUESTING: Final = "REQUESTING_HOST_POWER"


class HostPowerJobConflictError(RuntimeError):
    """Já existe uma ação de energia do host pendente ou em execução."""


class HostPowerRequestError(ValueError):
    """A confirmação forte da ação de energia é inválida."""


class SafePalworldShutdown(Protocol):
    def execute(
        self,
        countdown_minutes: int,
        stop_timeout_seconds: int,
        control: CountdownControl,
    ) -> AssistedShutdownResult: ...


@dataclass(frozen=True, slots=True)
class HostPowerJobView:
    id: int
    action: HostPowerAction
    status: str
    progress: int
    step: str
    failure: str | None
    palworld_initial_state: str | None
    host_command_requested: bool


class ImmediateShutdownControl:
    def update(self, remaining_seconds: int, total_seconds: int) -> CountdownDirective:
        del remaining_seconds, total_seconds
        return CountdownDirective.CONTINUE

    def mark_irreversible(self) -> CountdownDirective:
        return CountdownDirective.CONTINUE


class HostPowerJobExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        palworld_health: PalworldHealthChecker,
        palworld_shutdown: SafePalworldShutdown,
        host_power: HostPowerController,
    ) -> None:
        self._session_factory = session_factory
        self._palworld_health = palworld_health
        self._palworld_shutdown = palworld_shutdown
        self._host_power = host_power

    def execute(self, job_id: int) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.status != JOB_STATUS_RUNNING:
                raise ValueError("somente jobs em execução podem controlar a energia do host")
            action = parse_host_power_job_kind(job.kind)
            timeout_seconds = _stop_timeout(job)
            job.step = HOST_POWER_STEP_CHECKING_PALWORLD
            job.progress = 10

        snapshot = self._palworld_health.check()
        initial_state = snapshot.state
        palworld_handling = "already_offline"
        if initial_state is not PalworldHealthState.OFFLINE:
            with session_scope(self._session_factory) as session:
                job = session.get_one(Job, job_id)
                job.step = HOST_POWER_STEP_STOPPING_PALWORLD
                job.progress = 30
            shutdown_result = self._palworld_shutdown.execute(
                0,
                timeout_seconds,
                ImmediateShutdownControl(),
            )
            if shutdown_result.outcome is not ShutdownOutcome.SUCCEEDED:
                self._complete(
                    job_id,
                    action,
                    succeeded=False,
                    initial_state=initial_state,
                    palworld_handling="failed",
                    failure="palworld_shutdown_failed",
                    host_command_requested=False,
                )
                return
            palworld_handling = "stopped"

        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            job.step = HOST_POWER_STEP_REQUESTING
            job.progress = 90
            job.is_cancellable = False
        try:
            self._host_power.request(action)
        except HostPowerControlError:
            self._complete(
                job_id,
                action,
                succeeded=False,
                initial_state=initial_state,
                palworld_handling=palworld_handling,
                failure="host_power_request_failed",
                host_command_requested=False,
            )
            return
        self._complete(
            job_id,
            action,
            succeeded=True,
            initial_state=initial_state,
            palworld_handling=palworld_handling,
            failure=None,
            host_command_requested=True,
        )

    def fail_unexpected(self, job_id: int) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            action = parse_host_power_job_kind(job.kind)
            result = job.result or {}
            initial_state_value = result.get("palworld_initial_state")
            initial_state = (
                PalworldHealthState(initial_state_value)
                if isinstance(initial_state_value, str)
                and initial_state_value in {state.value for state in PalworldHealthState}
                else None
            )
        self._complete(
            job_id,
            action,
            succeeded=False,
            initial_state=initial_state,
            palworld_handling="unknown",
            failure="unexpected_failure",
            host_command_requested=False,
        )

    def _complete(
        self,
        job_id: int,
        action: HostPowerAction,
        *,
        succeeded: bool,
        initial_state: PalworldHealthState | None,
        palworld_handling: str,
        failure: str | None,
        host_command_requested: bool,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            finished_at = datetime.now(UTC)
            job.status = JOB_STATUS_SUCCEEDED if succeeded else JOB_STATUS_FAILED
            job.step = JOB_STEP_COMPLETED if succeeded else JOB_STEP_FAILED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = finished_at
            previous = dict(job.result or {})
            previous.update(
                {
                    "palworld_initial_state": initial_state.value if initial_state else None,
                    "palworld_handling": palworld_handling,
                    "host_command_requested": host_command_requested,
                    "failure": failure,
                }
            )
            job.result = previous
            record_audit_event(
                session,
                occurred_at=finished_at,
                action=f"HOST_{action.value}",
                result=AUDIT_RESULT_SUCCESS if succeeded else AUDIT_RESULT_FAILURE,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Ubuntu",
                details={
                    "palworld_initial_state": initial_state.value if initial_state else None,
                    "palworld_handling": palworld_handling,
                    "host_command_requested": host_command_requested,
                    "failure": failure,
                },
            )


def host_power_job_kind(action: HostPowerAction) -> str:
    return f"HOST_{action.value}"


def parse_host_power_job_kind(kind: str) -> HostPowerAction:
    if kind not in HOST_POWER_JOB_KINDS:
        raise ValueError("tipo de job não pertence ao controle de energia do host")
    return HostPowerAction(kind.removeprefix("HOST_"))


def enqueue_host_power_job(
    session: Session,
    action: HostPowerAction,
    *,
    confirmation: str,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    if confirmation != HOST_POWER_CONFIRMATIONS[action]:
        raise HostPowerRequestError(f"Digite {HOST_POWER_CONFIRMATIONS[action]} para confirmar.")
    active = session.scalar(
        select(Job.id).where(
            Job.coordination_key == HOST_POWER_COORDINATION_KEY,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    if active is not None:
        raise HostPowerJobConflictError("Já existe uma ação de energia do host em andamento.")
    timeout_seconds = lifecycle_timeout(session, LifecycleAction.STOP)
    job = Job(
        kind=host_power_job_kind(action),
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=False,
        requires_maintenance_lock=True,
        coordination_key=HOST_POWER_COORDINATION_KEY,
        result={"stop_timeout_seconds": timeout_seconds},
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise HostPowerJobConflictError(
            "Já existe uma ação de energia do host em andamento."
        ) from error
    requested_at = occurred_at or datetime.now(UTC)
    record_audit_event(
        session,
        occurred_at=requested_at,
        action=f"HOST_{action.value}_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target="Ubuntu",
    )
    return job


def latest_host_power_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job)
        .where(Job.kind.in_(HOST_POWER_JOB_KINDS))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )


def host_power_job_view(job: Job) -> HostPowerJobView:
    result = job.result or {}
    failure = result.get("failure")
    initial_state = result.get("palworld_initial_state")
    requested = result.get("host_command_requested")
    return HostPowerJobView(
        id=job.id,
        action=parse_host_power_job_kind(job.kind),
        status=job.status,
        progress=job.progress,
        step=job.step,
        failure=failure if isinstance(failure, str) else None,
        palworld_initial_state=initial_state if isinstance(initial_state, str) else None,
        host_command_requested=requested is True,
    )


def create_host_power_job_executor(
    settings: Settings,
    session_factory: sessionmaker[Session],
    palworld_shutdown: SafePalworldShutdown,
) -> HostPowerJobExecutor:
    if settings.environment is AppEnvironment.PRODUCTION:
        palworld_service = create_palworld_service(settings)
        health = create_palworld_health_check(settings, palworld_service)
    else:
        health = PersistentFakePalworldEnvironment(session_factory)
    return HostPowerJobExecutor(
        session_factory,
        health,
        palworld_shutdown,
        create_host_power_controller(settings),
    )


def _stop_timeout(job: Job) -> int:
    value = (job.result or {}).get("stop_timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300:
        raise ValueError("job possui stop_timeout_seconds inválido")
    return value
