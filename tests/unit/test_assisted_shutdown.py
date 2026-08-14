from app.health.palworld import PalworldHealthState
from app.integrations.palworld_rest import (
    FakePalworldShutdownCommunicator,
    PalworldRestOperationError,
)
from app.lifecycle.service import LifecycleAction, LifecycleOutcome, LifecycleResult
from app.shutdown.service import (
    AssistedShutdownExecutor,
    CountdownDirective,
    ForcedShutdownExecutor,
    ShutdownOutcome,
)
from app.system.palworld_service import PalworldSignal


class RecordingLifecycle:
    def __init__(self) -> None:
        self.calls: list[tuple[LifecycleAction, int]] = []

    def execute(self, action: LifecycleAction, timeout_seconds: int) -> LifecycleResult:
        self.calls.append((action, timeout_seconds))
        return LifecycleResult(LifecycleOutcome.SUCCEEDED, PalworldHealthState.OFFLINE, False)


class StaticControl:
    def __init__(self, directive: CountdownDirective) -> None:
        self.directive = directive
        self.updates: list[tuple[int, int]] = []
        self.irreversible = False

    def update(self, remaining_seconds: int, total_seconds: int) -> CountdownDirective:
        self.updates.append((remaining_seconds, total_seconds))
        return self.directive

    def mark_irreversible(self) -> CountdownDirective:
        self.irreversible = True
        return (
            self.directive
            if self.directive is CountdownDirective.CANCEL
            else CountdownDirective.CONTINUE
        )


def test_assisted_shutdown_can_be_cancelled_before_stop() -> None:
    communicator = FakePalworldShutdownCommunicator(online_players=2)
    lifecycle = RecordingLifecycle()
    control = StaticControl(CountdownDirective.CANCEL)
    executor = AssistedShutdownExecutor(communicator, lifecycle, sleep=lambda _seconds: None)

    result = executor.execute(5, 60, control)

    assert result.outcome is ShutdownOutcome.CANCELLED
    assert lifecycle.calls == []
    assert communicator.announcements == [
        "O servidor será desligado em 5 minutos.",
        "O desligamento do servidor foi cancelado.",
    ]


def test_execute_now_skips_countdown_but_uses_normal_stop() -> None:
    communicator = FakePalworldShutdownCommunicator(online_players=1)
    lifecycle = RecordingLifecycle()
    control = StaticControl(CountdownDirective.EXECUTE_NOW)
    executor = AssistedShutdownExecutor(communicator, lifecycle, sleep=lambda _seconds: None)

    result = executor.execute(10, 37, control)

    assert result.outcome is ShutdownOutcome.SUCCEEDED
    assert lifecycle.calls == [(LifecycleAction.STOP, 37)]
    assert control.updates == [(600, 600)]
    assert control.irreversible is True


class FailingCommunicator:
    def online_player_count(self) -> int:
        raise PalworldRestOperationError("falha simulada")

    def announce(self, message: str) -> None:
        del message


def test_player_query_failure_aborts_before_normal_stop() -> None:
    lifecycle = RecordingLifecycle()
    executor = AssistedShutdownExecutor(FailingCommunicator(), lifecycle)

    result = executor.execute(0, 60, StaticControl(CountdownDirective.CONTINUE))

    assert result.outcome is ShutdownOutcome.FAILED
    assert result.failure == "communication_failed"
    assert lifecycle.calls == []


class SignalEnvironment:
    def __init__(self) -> None:
        self.signals: list[PalworldSignal] = []

    def send_signal(self, signal: PalworldSignal) -> None:
        self.signals.append(signal)

    def check(self):  # type: ignore[no-untyped-def]
        from app.health.palworld import PalworldHealthSnapshot
        from app.integrations.palworld_rest import RestApiState

        return PalworldHealthSnapshot(
            PalworldHealthState.ONLINE, "active", True, RestApiState.AVAILABLE
        )

    def is_open(self) -> bool:
        return True


def test_sigterm_timeout_never_escalates_automatically_to_sigkill() -> None:
    environment = SignalEnvironment()
    clock_values = iter([0.0, 0.0, 0.0, 1.0])
    executor = ForcedShutdownExecutor(
        environment,
        environment,
        environment,
        monotonic=lambda: next(clock_values),
        sleep=lambda _seconds: None,
    )

    result = executor.execute(PalworldSignal.TERM, 1)

    assert result.outcome is ShutdownOutcome.FAILED
    assert result.timed_out is True
    assert environment.signals == [PalworldSignal.TERM]
