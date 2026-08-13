import subprocess
from collections.abc import Sequence

import psutil
import pytest

from app.config import AppEnvironment, Settings
from app.system.palworld_process import (
    FakePalworldProcessProbe,
    PalworldProcessQueryError,
    SystemdPalworldProcessProbe,
    create_palworld_process_probe,
)


class RecordingRunner:
    def __init__(
        self,
        *,
        stdout: str = "0\n",
        returncode: int = 0,
        error: OSError | subprocess.TimeoutExpired | None = None,
    ) -> None:
        self.result = subprocess.CompletedProcess(
            args=["/usr/bin/systemctl"],
            returncode=returncode,
            stdout=stdout,
            stderr="detalhe-privado",
        )
        self.error = error
        self.command: tuple[str, ...] | None = None
        self.timeout_seconds: float | None = None

    def __call__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        return self.result


def test_process_probe_queries_main_pid_and_inspects_it() -> None:
    runner = RecordingRunner(stdout="321\n")
    inspected: list[int] = []

    def inspector(pid: int) -> bool:
        inspected.append(pid)
        return True

    probe = SystemdPalworldProcessProbe(
        "palworld.service",
        runner=runner,
        inspector=inspector,
    )

    assert probe.is_running() is True
    assert inspected == [321]
    assert runner.command == (
        "/usr/bin/systemctl",
        "show",
        "--property=MainPID",
        "--value",
        "palworld.service",
    )
    assert runner.timeout_seconds == 5.0


def test_zero_main_pid_means_process_is_not_running() -> None:
    probe = SystemdPalworldProcessProbe(
        "palworld.service",
        runner=RecordingRunner(stdout="0\n"),
        inspector=lambda _pid: pytest.fail("PID zero não deve ser inspecionado"),
    )

    assert probe.is_running() is False


@pytest.mark.parametrize("stdout", ["", "-1", "12 34", "texto"])
def test_invalid_main_pid_becomes_safe_query_error(stdout: str) -> None:
    probe = SystemdPalworldProcessProbe(
        "palworld.service",
        runner=RecordingRunner(stdout=stdout),
    )

    with pytest.raises(PalworldProcessQueryError, match="PID inválido"):
        probe.is_running()


def test_process_command_failure_does_not_expose_stderr() -> None:
    probe = SystemdPalworldProcessProbe(
        "palworld.service",
        runner=RecordingRunner(returncode=1),
    )

    with pytest.raises(PalworldProcessQueryError) as error:
        probe.is_running()

    assert "detalhe-privado" not in str(error.value)


def test_psutil_errors_become_safe_query_errors() -> None:
    def denied(_pid: int) -> bool:
        raise psutil.AccessDenied(pid=321)

    probe = SystemdPalworldProcessProbe(
        "palworld.service",
        runner=RecordingRunner(stdout="321\n"),
        inspector=denied,
    )

    with pytest.raises(PalworldProcessQueryError, match="inspecionar"):
        probe.is_running()


def test_fake_process_probe_is_controllable() -> None:
    probe = FakePalworldProcessProbe()

    assert probe.is_running() is False
    probe.set_running(True)
    assert probe.is_running() is True


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_non_production_environments_use_fake_process_probe(
    environment: AppEnvironment,
) -> None:
    probe = create_palworld_process_probe(Settings(environment=environment))

    assert isinstance(probe, FakePalworldProcessProbe)
