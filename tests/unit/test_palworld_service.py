import subprocess
from collections.abc import Sequence
from ipaddress import ip_address

import pytest

from app.config import AppEnvironment, Settings
from app.system.palworld_service import (
    SYSTEMCTL_PATH,
    FakePalworldService,
    PalworldServiceQueryError,
    SystemdPalworldService,
    create_palworld_service,
)


class RecordingRunner:
    def __init__(
        self,
        result: subprocess.CompletedProcess[str] | None = None,
        error: OSError | subprocess.TimeoutExpired | None = None,
    ) -> None:
        self.result = result
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
        assert self.result is not None
        return self.result


def completed_process(
    *,
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[SYSTEMCTL_PATH],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_systemd_adapter_queries_only_the_configured_service() -> None:
    runner = RecordingRunner(completed_process(stdout="active\n"))
    service = SystemdPalworldService("palworld.service", runner=runner)

    status = service.get_status()

    assert status.active is True
    assert status.source_state == "active"
    assert runner.command == (
        "/usr/bin/systemctl",
        "show",
        "--property=ActiveState",
        "--value",
        "palworld.service",
    )
    assert runner.timeout_seconds == 5.0


@pytest.mark.parametrize("source_state", ["inactive", "failed", "activating"])
def test_systemd_non_active_states_are_reported_as_inactive(source_state: str) -> None:
    service = SystemdPalworldService(
        "palworld.service",
        runner=RecordingRunner(completed_process(stdout=f"{source_state}\n")),
    )

    status = service.get_status()

    assert status.active is False
    assert status.source_state == source_state


@pytest.mark.parametrize(
    "service_name",
    ["--all.service", "palworld.service --no-pager", "../palworld.service", "palworld"],
)
def test_systemd_adapter_rejects_untrusted_service_names(service_name: str) -> None:
    with pytest.raises(ValueError, match="inválido"):
        SystemdPalworldService(service_name)


def test_systemd_command_failure_does_not_expose_stderr() -> None:
    private_detail = "detalhe-privado-do-host"
    service = SystemdPalworldService(
        "palworld.service",
        runner=RecordingRunner(completed_process(stdout="", returncode=1, stderr=private_detail)),
    )

    with pytest.raises(PalworldServiceQueryError) as error:
        service.get_status()

    assert private_detail not in str(error.value)


@pytest.mark.parametrize(
    "runner_error",
    [
        OSError("falha local"),
        subprocess.TimeoutExpired(cmd=[SYSTEMCTL_PATH], timeout=5),
    ],
)
def test_systemd_execution_errors_become_safe_query_errors(
    runner_error: OSError | subprocess.TimeoutExpired,
) -> None:
    service = SystemdPalworldService(
        "palworld.service",
        runner=RecordingRunner(error=runner_error),
    )

    with pytest.raises(PalworldServiceQueryError, match="Não foi possível"):
        service.get_status()


def test_systemd_adapter_rejects_invalid_state_output() -> None:
    service = SystemdPalworldService(
        "palworld.service",
        runner=RecordingRunner(completed_process(stdout="active\nconteúdo inesperado")),
    )

    with pytest.raises(PalworldServiceQueryError, match="estado inválido"):
        service.get_status()


def test_fake_service_is_controllable_without_systemd() -> None:
    service = FakePalworldService()

    assert service.get_status().active is False
    service.set_active(True)
    assert service.get_status().active is True


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_non_production_environments_use_fake_service(environment: AppEnvironment) -> None:
    service = create_palworld_service(Settings(environment=environment))

    assert isinstance(service, FakePalworldService)


def test_production_uses_systemd_adapter() -> None:
    service = create_palworld_service(
        Settings(environment=AppEnvironment.PRODUCTION, app_host=ip_address("127.0.0.1"))
    )

    assert isinstance(service, SystemdPalworldService)
