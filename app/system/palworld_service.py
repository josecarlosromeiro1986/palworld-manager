import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.config import SERVICE_NAME_PATTERN, AppEnvironment, Settings

SYSTEMCTL_PATH = "/usr/bin/systemctl"
SYSTEMCTL_QUERY_TIMEOUT_SECONDS = 5.0
SERVICE_NAME_REGEX = re.compile(SERVICE_NAME_PATTERN)
SYSTEMD_STATE_PATTERN = re.compile(r"^[a-z][a-z-]{0,63}$")


class PalworldServiceQueryError(RuntimeError):
    """A consulta ao gerenciador de serviços não produziu um estado confiável."""


@dataclass(frozen=True, slots=True)
class PalworldServiceStatus:
    active: bool
    source_state: str


class PalworldService(Protocol):
    def get_status(self) -> PalworldServiceStatus: ...


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
    )


class SystemdPalworldService:
    def __init__(
        self,
        service_name: str,
        *,
        runner: CommandRunner = _run_command,
        timeout_seconds: float = SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        if SERVICE_NAME_REGEX.fullmatch(service_name) is None:
            raise ValueError("nome de serviço systemd inválido")
        if timeout_seconds <= 0:
            raise ValueError("o timeout da consulta deve ser positivo")
        self._service_name = service_name
        self._runner = runner
        self._timeout_seconds = timeout_seconds

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


def create_palworld_service(settings: Settings) -> PalworldService:
    if settings.environment is AppEnvironment.PRODUCTION:
        return SystemdPalworldService(settings.palworld_service)
    return FakePalworldService()
