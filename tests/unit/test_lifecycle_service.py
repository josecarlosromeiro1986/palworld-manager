from collections.abc import Sequence

import pytest

from app.config import Settings
from app.health.palworld import PalworldHealthSnapshot, PalworldHealthState
from app.integrations.palworld_rest import RestApiState
from app.lifecycle.service import (
    FakeLifecycleEnvironment,
    LifecycleAction,
    LifecycleOutcome,
    PalworldLifecycleExecutor,
    create_lifecycle_executor,
)
from app.system.palworld_service import (
    PalworldServiceControlError,
    PalworldServiceStatus,
)


class RecordingController:
    def __init__(self, *, fail: bool = False) -> None:
        self.actions: list[LifecycleAction] = []
        self.fail = fail

    def get_status(self) -> PalworldServiceStatus:
        return PalworldServiceStatus(active=False, source_state="inactive")

    def _record(self, action: LifecycleAction) -> None:
        self.actions.append(action)
        if self.fail:
            raise PalworldServiceControlError("falha simulada")

    def start(self) -> None:
        self._record(LifecycleAction.START)

    def stop(self) -> None:
        self._record(LifecycleAction.STOP)

    def restart(self) -> None:
        self._record(LifecycleAction.RESTART)


class SequenceHealth:
    def __init__(self, states: Sequence[PalworldHealthState]) -> None:
        self.states = list(states)
        self.index = 0

    def check(self) -> PalworldHealthSnapshot:
        state = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return PalworldHealthSnapshot(
            state=state,
            service_state="active",
            process_running=True,
            rest_api_state=RestApiState.AVAILABLE,
        )


class SequencePort:
    def __init__(self, values: Sequence[bool]) -> None:
        self.values = list(values)
        self.index = 0

    def is_open(self) -> bool:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("action", [LifecycleAction.START, LifecycleAction.RESTART])
def test_start_and_restart_wait_until_online(action: LifecycleAction) -> None:
    controller = RecordingController()
    clock = Clock()
    executor = PalworldLifecycleExecutor(
        controller,
        SequenceHealth([PalworldHealthState.STARTING, PalworldHealthState.ONLINE]),
        SequencePort([True]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = executor.execute(action, 120)

    assert controller.actions == [action]
    assert result.outcome is LifecycleOutcome.SUCCEEDED
    assert result.final_state is PalworldHealthState.ONLINE
    assert result.timed_out is False


def test_stop_requires_offline_health_and_closed_rest_port() -> None:
    controller = RecordingController()
    clock = Clock()
    executor = PalworldLifecycleExecutor(
        controller,
        SequenceHealth([PalworldHealthState.OFFLINE, PalworldHealthState.OFFLINE]),
        SequencePort([True, False]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = executor.execute(LifecycleAction.STOP, 60)

    assert controller.actions == [LifecycleAction.STOP]
    assert result.outcome is LifecycleOutcome.SUCCEEDED
    assert result.final_state is PalworldHealthState.OFFLINE
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize(
    ("action", "timeout_seconds"),
    [
        (LifecycleAction.START, 120),
        (LifecycleAction.RESTART, 120),
        (LifecycleAction.STOP, 60),
    ],
)
def test_lifecycle_timeout_is_respected_exactly(
    action: LifecycleAction,
    timeout_seconds: int,
) -> None:
    clock = Clock()
    executor = PalworldLifecycleExecutor(
        RecordingController(),
        SequenceHealth([PalworldHealthState.STARTING]),
        SequencePort([True]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = executor.execute(action, timeout_seconds)

    assert result.outcome is LifecycleOutcome.FAILED
    assert result.timed_out is True
    assert sum(clock.sleeps) == timeout_seconds


def test_control_failure_returns_failure_without_waiting() -> None:
    clock = Clock()
    executor = PalworldLifecycleExecutor(
        RecordingController(fail=True),
        SequenceHealth([PalworldHealthState.ONLINE]),
        SequencePort([True]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = executor.execute(LifecycleAction.START, 120)

    assert result.outcome is LifecycleOutcome.FAILED
    assert result.timed_out is False
    assert clock.sleeps == []


def test_development_uses_complete_lifecycle_fake() -> None:
    executor = create_lifecycle_executor(Settings())

    assert isinstance(executor, PalworldLifecycleExecutor)
    assert executor.execute(LifecycleAction.START, 120).outcome is LifecycleOutcome.SUCCEEDED


def test_fake_environment_supports_all_component_contracts() -> None:
    fake = FakeLifecycleEnvironment()

    assert fake.check().state is PalworldHealthState.OFFLINE
    fake.start()
    assert fake.get_status().active is True
    assert fake.is_open() is True
    assert fake.check().state is PalworldHealthState.ONLINE
