import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from sqlalchemy.orm import Session, sessionmaker

from app.config import AppEnvironment, Settings
from app.health.palworld import (
    PalworldHealthChecker,
    PalworldHealthState,
    create_palworld_health_check,
)
from app.integrations.palworld_rest import (
    PalworldRestOperationError,
    PalworldShutdownCommunicator,
    create_palworld_shutdown_communicator,
)
from app.lifecycle.fake import PersistentFakePalworldEnvironment
from app.lifecycle.service import (
    LifecycleAction,
    LifecycleExecutor,
    LifecycleOutcome,
    PalworldLifecycleExecutor,
)
from app.system.palworld_service import (
    PalworldServiceControlError,
    PalworldSignal,
    PalworldSignalController,
    create_palworld_service,
)
from app.system.port_probe import PortProbe, TcpPortProbe


class CountdownDirective(StrEnum):
    CONTINUE = "CONTINUE"
    CANCEL = "CANCEL"
    EXECUTE_NOW = "EXECUTE_NOW"


class ShutdownOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class AssistedShutdownResult:
    outcome: ShutdownOutcome
    online_players: int | None
    timed_out: bool
    final_state: PalworldHealthState | None
    failure: str | None = None


class CountdownControl(Protocol):
    def update(self, remaining_seconds: int, total_seconds: int) -> CountdownDirective: ...

    def mark_irreversible(self) -> CountdownDirective: ...


class AssistedShutdownExecutor:
    def __init__(
        self,
        communicator: PalworldShutdownCommunicator,
        lifecycle: LifecycleExecutor,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._communicator = communicator
        self._lifecycle = lifecycle
        self._sleep = sleep

    def execute(
        self,
        countdown_minutes: int,
        stop_timeout_seconds: int,
        control: CountdownControl,
    ) -> AssistedShutdownResult:
        if countdown_minutes not in {0, 1, 5, 10}:
            raise ValueError("duração de desligamento assistido inválida")
        try:
            players = self._communicator.online_player_count()
            if players and countdown_minutes:
                unit = "minuto" if countdown_minutes == 1 else "minutos"
                self._communicator.announce(
                    f"O servidor será desligado em {countdown_minutes} {unit}."
                )
        except PalworldRestOperationError:
            return AssistedShutdownResult(
                ShutdownOutcome.FAILED, None, False, None, "communication_failed"
            )

        total = countdown_minutes * 60
        remaining = total
        while remaining > 0:
            directive = control.update(remaining, total)
            if directive is CountdownDirective.CANCEL:
                if players:
                    with suppress(PalworldRestOperationError):
                        self._communicator.announce("O desligamento do servidor foi cancelado.")
                return AssistedShutdownResult(ShutdownOutcome.CANCELLED, players, False, None)
            if directive is CountdownDirective.EXECUTE_NOW:
                break
            self._sleep(1.0)
            remaining -= 1

        directive = control.mark_irreversible()
        if directive is CountdownDirective.CANCEL:
            return AssistedShutdownResult(ShutdownOutcome.CANCELLED, players, False, None)
        if players:
            try:
                self._communicator.announce("O servidor será desligado agora.")
            except PalworldRestOperationError:
                return AssistedShutdownResult(
                    ShutdownOutcome.FAILED, players, False, None, "communication_failed"
                )
        lifecycle_result = self._lifecycle.execute(LifecycleAction.STOP, stop_timeout_seconds)
        return AssistedShutdownResult(
            ShutdownOutcome.SUCCEEDED
            if lifecycle_result.outcome is LifecycleOutcome.SUCCEEDED
            else ShutdownOutcome.FAILED,
            players,
            lifecycle_result.timed_out,
            lifecycle_result.final_state,
            None if lifecycle_result.outcome is LifecycleOutcome.SUCCEEDED else "stop_failed",
        )


class ForcedShutdownExecutor:
    def __init__(
        self,
        signals: PalworldSignalController,
        health: PalworldHealthChecker,
        rest_port: PortProbe,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._signals = signals
        self._health = health
        self._rest_port = rest_port
        self._monotonic = monotonic
        self._sleep = sleep

    def execute(self, signal: PalworldSignal, timeout_seconds: int) -> AssistedShutdownResult:
        try:
            self._signals.send_signal(signal)
        except PalworldServiceControlError:
            return AssistedShutdownResult(
                ShutdownOutcome.FAILED, None, False, PalworldHealthState.FAILURE, "signal_failed"
            )
        deadline = self._monotonic() + timeout_seconds
        while True:
            snapshot = self._health.check()
            if snapshot.state is PalworldHealthState.OFFLINE and not self._rest_port.is_open():
                return AssistedShutdownResult(
                    ShutdownOutcome.SUCCEEDED, None, False, snapshot.state
                )
            if self._monotonic() >= deadline:
                return AssistedShutdownResult(
                    ShutdownOutcome.FAILED, None, True, snapshot.state, "signal_timeout"
                )
            self._sleep(min(1.0, max(0.0, deadline - self._monotonic())))


def create_shutdown_executors(
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> tuple[AssistedShutdownExecutor, ForcedShutdownExecutor]:
    communicator = create_palworld_shutdown_communicator(settings)
    if settings.environment is not AppEnvironment.PRODUCTION:
        fake = PersistentFakePalworldEnvironment(session_factory)
        lifecycle = PalworldLifecycleExecutor(fake, fake, fake)
        return AssistedShutdownExecutor(communicator, lifecycle), ForcedShutdownExecutor(
            fake, fake, fake
        )
    controller = create_palworld_service(settings)
    health = create_palworld_health_check(settings, controller)
    rest_url = settings.palworld_rest_base_url
    host = rest_url.host
    if host is None:
        raise ValueError("PALWORLD_REST_BASE_URL precisa informar um host")
    port = rest_url.port or (443 if rest_url.scheme == "https" else 80)
    rest_port = TcpPortProbe(host, port)
    lifecycle = PalworldLifecycleExecutor(controller, health, rest_port)
    return AssistedShutdownExecutor(communicator, lifecycle), ForcedShutdownExecutor(
        cast(PalworldSignalController, controller), health, rest_port
    )
