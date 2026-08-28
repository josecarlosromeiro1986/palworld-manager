import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.config import AppEnvironment, Settings
from app.health.palworld import (
    PalworldHealthChecker,
    PalworldHealthSnapshot,
    PalworldHealthState,
    create_palworld_health_check,
)
from app.integrations.palworld_rest import RestApiState
from app.lifecycle.fake import PersistentFakePalworldEnvironment
from app.system.palworld_service import (
    FakePalworldService,
    PalworldServiceControlError,
    PalworldServiceController,
    PalworldServiceStatus,
    create_palworld_service,
)
from app.system.port_probe import PortProbe, TcpPortProbe

POLL_INTERVAL_SECONDS = 1.0


class LifecycleAction(StrEnum):
    START = "START"
    STOP = "STOP"
    RESTART = "RESTART"


class LifecycleOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    outcome: LifecycleOutcome
    final_state: PalworldHealthState
    timed_out: bool


class LifecycleExecutor(Protocol):
    def execute(self, action: LifecycleAction, timeout_seconds: int) -> LifecycleResult: ...


class PalworldLifecycleExecutor:
    def __init__(
        self,
        controller: PalworldServiceController,
        health: PalworldHealthChecker,
        rest_port: PortProbe,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("o intervalo de consulta deve ser positivo")
        self._controller = controller
        self._health = health
        self._rest_port = rest_port
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval_seconds = poll_interval_seconds

    def execute(self, action: LifecycleAction, timeout_seconds: int) -> LifecycleResult:
        if timeout_seconds <= 0:
            raise ValueError("o timeout deve ser positivo")

        restart_transition_observed = True
        if action is LifecycleAction.RESTART:
            restart_transition_observed = (
                self._health.check().state is not PalworldHealthState.ONLINE
            )

        try:
            if action is LifecycleAction.START:
                self._controller.start()
            elif action is LifecycleAction.STOP:
                self._controller.stop()
            else:
                self._controller.restart()
        except PalworldServiceControlError:
            return LifecycleResult(
                LifecycleOutcome.FAILED,
                PalworldHealthState.FAILURE,
                timed_out=False,
            )

        deadline = self._monotonic() + timeout_seconds
        while True:
            snapshot = self._health.check()
            if (
                action is LifecycleAction.RESTART
                and snapshot.state is not PalworldHealthState.ONLINE
            ):
                restart_transition_observed = True
            if restart_transition_observed and self._reached_target(action, snapshot):
                return LifecycleResult(
                    LifecycleOutcome.SUCCEEDED,
                    snapshot.state,
                    timed_out=False,
                )
            if self._monotonic() >= deadline:
                return LifecycleResult(
                    LifecycleOutcome.FAILED,
                    snapshot.state,
                    timed_out=True,
                )
            self._sleep(min(self._poll_interval_seconds, max(0.0, deadline - self._monotonic())))

    def _reached_target(
        self,
        action: LifecycleAction,
        snapshot: PalworldHealthSnapshot,
    ) -> bool:
        if action is not LifecycleAction.STOP:
            return snapshot.state is PalworldHealthState.ONLINE
        return snapshot.state is PalworldHealthState.OFFLINE and not self._rest_port.is_open()


class FakeLifecycleEnvironment(PalworldHealthChecker, PalworldServiceController, PortProbe):
    def __init__(self) -> None:
        self._active = False
        self._restart_pending = False

    def get_status(self) -> PalworldServiceStatus:
        return FakePalworldService(active=self._active).get_status()

    def start(self) -> None:
        self._active = True
        self._restart_pending = False

    def stop(self) -> None:
        self._active = False
        self._restart_pending = False

    def restart(self) -> None:
        self._active = True
        self._restart_pending = True

    def is_open(self) -> bool:
        return self._active

    def check(self) -> PalworldHealthSnapshot:
        if self._restart_pending:
            self._restart_pending = False
            return PalworldHealthSnapshot(
                state=PalworldHealthState.STARTING,
                service_state="activating",
                process_running=True,
                rest_api_state=RestApiState.UNAVAILABLE,
            )
        return PalworldHealthSnapshot(
            state=PalworldHealthState.ONLINE if self._active else PalworldHealthState.OFFLINE,
            service_state="active" if self._active else "inactive",
            process_running=self._active,
            rest_api_state=(RestApiState.AVAILABLE if self._active else RestApiState.UNAVAILABLE),
        )


def create_lifecycle_executor(
    settings: Settings,
    session_factory: sessionmaker[Session] | None = None,
) -> LifecycleExecutor:
    if settings.environment is not AppEnvironment.PRODUCTION:
        fake = (
            PersistentFakePalworldEnvironment(session_factory)
            if session_factory is not None
            else FakeLifecycleEnvironment()
        )
        return PalworldLifecycleExecutor(fake, fake, fake)

    controller = create_palworld_service(settings)
    health = create_palworld_health_check(settings, controller)
    rest_url = settings.palworld_rest_base_url
    host = rest_url.host
    if host is None:
        raise ValueError("PALWORLD_REST_BASE_URL precisa informar um host")
    port = rest_url.port or (443 if rest_url.scheme == "https" else 80)
    return PalworldLifecycleExecutor(controller, health, TcpPortProbe(host, port))
