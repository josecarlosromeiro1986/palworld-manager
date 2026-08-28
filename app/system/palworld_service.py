import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.config import SERVICE_NAME_PATTERN, AppEnvironment, Settings
from app.system.commands import sanitized_subprocess_environment
from app.system.host_control import (
    MANAGED_PALWORLD_SERVICE,
    SYSTEMCTL_PATH,
    PrivilegedHostAction,
    host_control_command,
)

SYSTEMCTL_QUERY_TIMEOUT_SECONDS = 5.0
SYSTEMCTL_CONTROL_TIMEOUT_SECONDS = 15.0
SERVICE_NAME_REGEX = re.compile(SERVICE_NAME_PATTERN)
SYSTEMD_STATE_PATTERN = re.compile(r"^[a-z][a-z-]{0,63}$")


class PalworldServiceQueryError(RuntimeError):
    """A consulta ao gerenciador de serviços não produziu um estado confiável."""


class PalworldServiceControlError(RuntimeError):
    """O systemd não confirmou uma ação de ciclo de vida do Palworld."""


class PalworldSignal(StrEnum):
    TERM = "SIGTERM"
    KILL = "SIGKILL"


@dataclass(frozen=True, slots=True)
class PalworldServiceStatus:
    active: bool
    source_state: str


class PalworldService(Protocol):
    def get_status(self) -> PalworldServiceStatus: ...


class PalworldServiceController(PalworldService, Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def restart(self) -> None: ...


class PalworldSignalController(Protocol):
    def send_signal(self, signal: PalworldSignal) -> None: ...


class CommandRunner(Protocol):
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


class SystemdPalworldService:
    def __init__(
        self,
        service_name: str,
        *,
        runner: CommandRunner = _run_command,
        timeout_seconds: float = SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
        control_timeout_seconds: float = SYSTEMCTL_CONTROL_TIMEOUT_SECONDS,
    ) -> None:
        if SERVICE_NAME_REGEX.fullmatch(service_name) is None:
            raise ValueError("nome de serviço systemd inválido")
        if service_name != MANAGED_PALWORLD_SERVICE:
            raise ValueError("serviço systemd não autorizado para controle em produção")
        if timeout_seconds <= 0:
            raise ValueError("o timeout da consulta deve ser positivo")
        if control_timeout_seconds <= 0:
            raise ValueError("o timeout de controle deve ser positivo")
        self._service_name = service_name
        self._runner = runner
        self._timeout_seconds = timeout_seconds
        self._control_timeout_seconds = control_timeout_seconds

    def get_status(self) -> PalworldServiceStatus:
        command = (
            SYSTEMCTL_PATH,
            "show",
            "--property=ActiveState",
            "--value",
            self._service_name,
        )
        try:
            result = self._runner(command, timeout_seconds=self._timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PalworldServiceQueryError(
                "Não foi possível consultar o estado do serviço Palworld."
            ) from error

        if result.returncode != 0:
            raise PalworldServiceQueryError(
                "Não foi possível consultar o estado do serviço Palworld."
            )

        source_state = result.stdout.strip()
        if SYSTEMD_STATE_PATTERN.fullmatch(source_state) is None:
            raise PalworldServiceQueryError(
                "O systemd retornou um estado inválido para o serviço Palworld."
            )
        return PalworldServiceStatus(
            active=source_state == "active",
            source_state=source_state,
        )

    def start(self) -> None:
        self._control("start")

    def stop(self) -> None:
        self._control("stop")

    def restart(self) -> None:
        self._control("restart")

    def send_signal(self, signal: PalworldSignal) -> None:
        if not isinstance(signal, PalworldSignal):
            raise ValueError("sinal do serviço Palworld inválido")
        action = {
            PalworldSignal.TERM: PrivilegedHostAction.PALWORLD_SIGTERM,
            PalworldSignal.KILL: PrivilegedHostAction.PALWORLD_SIGKILL,
        }[signal]
        command = host_control_command(action)
        try:
            result = self._runner(command, timeout_seconds=self._control_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PalworldServiceControlError(
                "Não foi possível sinalizar o serviço Palworld."
            ) from error
        if result.returncode != 0:
            raise PalworldServiceControlError("Não foi possível sinalizar o serviço Palworld.")

    def _control(self, action: str) -> None:
        privileged_action = {
            "start": PrivilegedHostAction.PALWORLD_START,
            "stop": PrivilegedHostAction.PALWORLD_STOP,
            "restart": PrivilegedHostAction.PALWORLD_RESTART,
        }.get(action)
        if privileged_action is None:
            raise ValueError("ação de serviço inválida")
        command = host_control_command(privileged_action)
        try:
            result = self._runner(command, timeout_seconds=self._control_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PalworldServiceControlError(
                "Não foi possível controlar o serviço Palworld."
            ) from error
        if result.returncode != 0:
            raise PalworldServiceControlError("Não foi possível controlar o serviço Palworld.")


class FakePalworldService:
    def __init__(self, *, active: bool = False) -> None:
        self._active = active

    def get_status(self) -> PalworldServiceStatus:
        return PalworldServiceStatus(
            active=self._active,
            source_state="active" if self._active else "inactive",
        )

    def set_active(self, active: bool) -> None:
        self._active = active

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def restart(self) -> None:
        self._active = True

    def send_signal(self, signal: PalworldSignal) -> None:
        del signal
        self._active = False


def create_palworld_service(settings: Settings) -> PalworldServiceController:
    if settings.environment is AppEnvironment.PRODUCTION:
        return SystemdPalworldService(settings.palworld_service)
    return FakePalworldService()
