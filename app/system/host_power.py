import subprocess
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from app.config import AppEnvironment, Settings
from app.system.commands import sanitized_subprocess_environment
from app.system.host_control import PrivilegedHostAction, host_control_command

HOST_POWER_TIMEOUT_SECONDS = 15.0


class HostPowerAction(StrEnum):
    REBOOT = "REBOOT"
    SHUTDOWN = "SHUTDOWN"


class HostPowerControlError(RuntimeError):
    """O systemd não aceitou a solicitação de energia do host."""


class HostPowerController(Protocol):
    def request(self, action: HostPowerAction) -> None: ...


class HostPowerCommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]: ...


def _run_command(
    command: Sequence[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        timeout=timeout_seconds,
        env=sanitized_subprocess_environment(),
    )


class SystemdHostPowerController:
    def __init__(
        self,
        *,
        runner: HostPowerCommandRunner = _run_command,
        timeout_seconds: float = HOST_POWER_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("o timeout do controle de energia deve ser positivo")
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def request(self, action: HostPowerAction) -> None:
        if not isinstance(action, HostPowerAction):
            raise ValueError("ação de energia do host inválida")
        privileged_action = {
            HostPowerAction.REBOOT: PrivilegedHostAction.HOST_REBOOT,
            HostPowerAction.SHUTDOWN: PrivilegedHostAction.HOST_POWEROFF,
        }[action]
        command = host_control_command(privileged_action)
        try:
            result = self._runner(command, timeout_seconds=self._timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HostPowerControlError(
                "Não foi possível solicitar a ação de energia do host."
            ) from error
        if result.returncode != 0:
            raise HostPowerControlError("Não foi possível solicitar a ação de energia do host.")


class FakeHostPowerController:
    def __init__(self) -> None:
        self.requests: list[HostPowerAction] = []

    def request(self, action: HostPowerAction) -> None:
        if not isinstance(action, HostPowerAction):
            raise ValueError("ação de energia do host inválida")
        self.requests.append(action)


def create_host_power_controller(settings: Settings) -> HostPowerController:
    if settings.environment is AppEnvironment.PRODUCTION:
        return SystemdHostPowerController()
    return FakeHostPowerController()
