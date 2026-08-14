import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import WorkerHeartbeat
from app.jobs.heartbeat import WORKER_HEARTBEAT_KEY, WORKER_LEASE_TIMEOUT_SECONDS
from app.system.palworld_service import CommandRunner, _run_command

WORKER_SERVICE_NAME = "palworld-manager-worker.service"
WORKER_HEALTH_THRESHOLD_SECONDS = WORKER_LEASE_TIMEOUT_SECONDS
SYSTEMCTL_PATH = "/usr/bin/systemctl"
SYSTEMCTL_QUERY_TIMEOUT_SECONDS = 5.0
SYSTEMD_STATE_PATTERN = re.compile(r"^[a-z][a-z-]{0,63}$")


class WorkerHealthState(StrEnum):
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    UNRESPONSIVE = "UNRESPONSIVE"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True, slots=True)
class WorkerServiceStatus:
    active: bool
    source_state: str
    active_for_seconds: float


@dataclass(frozen=True, slots=True)
class WorkerHealthSnapshot:
    state: WorkerHealthState
    service_state: str
    heartbeat_age_seconds: float | None
    worker_id: str | None


class WorkerServiceQueryError(RuntimeError):
    """O estado do serviço do worker não pôde ser determinado com segurança."""


class WorkerService(Protocol):
    def get_status(self) -> WorkerServiceStatus: ...


class SystemdWorkerService:
    def __init__(
        self,
        *,
        runner: CommandRunner = _run_command,
        boot_time_seconds: Callable[[], float] = lambda: time.clock_gettime(time.CLOCK_BOOTTIME),
    ) -> None:
        self._runner = runner
        self._boot_time_seconds = boot_time_seconds

    def get_status(self) -> WorkerServiceStatus:
        command = (
            SYSTEMCTL_PATH,
            "show",
            "--property=ActiveState,ActiveEnterTimestampMonotonic",
            WORKER_SERVICE_NAME,
        )
        try:
            result = self._runner(command, timeout_seconds=SYSTEMCTL_QUERY_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkerServiceQueryError(
                "Não foi possível consultar o serviço do worker."
            ) from error
        if result.returncode != 0:
            raise WorkerServiceQueryError("Não foi possível consultar o serviço do worker.")

        properties = _parse_systemd_properties(result.stdout)
        source_state = properties.get("ActiveState", "")
        activated_raw = properties.get("ActiveEnterTimestampMonotonic", "")
        if SYSTEMD_STATE_PATTERN.fullmatch(source_state) is None or not activated_raw.isdigit():
            raise WorkerServiceQueryError("O systemd retornou um estado inválido para o worker.")
        activated_seconds = int(activated_raw) / 1_000_000
        active_for = max(float(self._boot_time_seconds()) - activated_seconds, 0.0)
        return WorkerServiceStatus(source_state == "active", source_state, active_for)


class FakeWorkerService:
    def __init__(
        self,
        *,
        active: bool = True,
        active_for_seconds: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.active = active
        self._monotonic = monotonic
        self._activated_at = monotonic() - max(active_for_seconds, 0.0)

    def get_status(self) -> WorkerServiceStatus:
        return WorkerServiceStatus(
            active=self.active,
            source_state="active" if self.active else "inactive",
            active_for_seconds=max(self._monotonic() - self._activated_at, 0.0),
        )


class WorkerHealthChecker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        service: WorkerService,
    ) -> None:
        self._session_factory = session_factory
        self._service = service

    def check(self, *, checked_at: datetime | None = None) -> WorkerHealthSnapshot:
        now = checked_at or datetime.now(UTC)
        try:
            service = self._service.get_status()
        except WorkerServiceQueryError:
            return WorkerHealthSnapshot(
                WorkerHealthState.UNRESPONSIVE,
                "unavailable",
                None,
                None,
            )
        if not service.active:
            return WorkerHealthSnapshot(
                WorkerHealthState.OFFLINE,
                service.source_state,
                None,
                None,
            )

        with session_scope(self._session_factory) as session:
            heartbeat = session.get(WorkerHeartbeat, WORKER_HEARTBEAT_KEY)
            if heartbeat is None:
                return _without_heartbeat(service)
            heartbeat_at = _as_utc(heartbeat.heartbeat_at)
            heartbeat_age = max((now - heartbeat_at).total_seconds(), 0.0)
            if heartbeat_age > service.active_for_seconds + 1.0:
                return _without_heartbeat(service)
            state = (
                WorkerHealthState.HEALTHY
                if heartbeat_age < WORKER_HEALTH_THRESHOLD_SECONDS
                else WorkerHealthState.UNRESPONSIVE
            )
            return WorkerHealthSnapshot(
                state,
                service.source_state,
                heartbeat_age,
                heartbeat.worker_id,
            )


def _without_heartbeat(service: WorkerServiceStatus) -> WorkerHealthSnapshot:
    state = (
        WorkerHealthState.STARTING
        if service.active_for_seconds < WORKER_HEALTH_THRESHOLD_SECONDS
        else WorkerHealthState.UNRESPONSIVE
    )
    return WorkerHealthSnapshot(state, service.source_state, None, None)


def _parse_systemd_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ActiveState", "ActiveEnterTimestampMonotonic"}:
            properties[key] = value.strip()
    return properties


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
