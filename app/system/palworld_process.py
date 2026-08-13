import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Protocol

import psutil

from app.config import SERVICE_NAME_PATTERN, AppEnvironment, Settings
from app.system.palworld_service import (
    SYSTEMCTL_PATH,
    SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
    CommandRunner,
    _run_command,
)

SERVICE_NAME_REGEX = re.compile(SERVICE_NAME_PATTERN)
PID_PATTERN = re.compile(r"^[0-9]+$")


class PalworldProcessQueryError(RuntimeError):
    """O processo principal do Palworld não pôde ser consultado com segurança."""


class PalworldProcessProbe(Protocol):
    def is_running(self) -> bool: ...


def _is_process_running(pid: int) -> bool:
    process = psutil.Process(pid)
    return process.is_running() and process.status() != psutil.STATUS_ZOMBIE


class SystemdPalworldProcessProbe:
    def __init__(
        self,
        service_name: str,
        *,
        runner: CommandRunner = _run_command,
        inspector: Callable[[int], bool] = _is_process_running,
        timeout_seconds: float = SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        if SERVICE_NAME_REGEX.fullmatch(service_name) is None:
            raise ValueError("nome de serviço systemd inválido")
        if timeout_seconds <= 0:
            raise ValueError("o timeout da consulta deve ser positivo")
        self._service_name = service_name
        self._runner = runner
        self._inspector = inspector
        self._timeout_seconds = timeout_seconds

    def is_running(self) -> bool:
        command: Sequence[str] = (
            SYSTEMCTL_PATH,
            "show",
            "--property=MainPID",
            "--value",
            self._service_name,
        )
        try:
            result = self._runner(command, timeout_seconds=self._timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PalworldProcessQueryError(
                "Não foi possível consultar o processo do Palworld."
            ) from error

        if result.returncode != 0:
            raise PalworldProcessQueryError("Não foi possível consultar o processo do Palworld.")

        raw_pid = result.stdout.strip()
        if PID_PATTERN.fullmatch(raw_pid) is None:
            raise PalworldProcessQueryError("O systemd retornou um PID inválido para o Palworld.")
        pid = int(raw_pid)
        if pid == 0:
            return False

        try:
            return self._inspector(pid)
        except psutil.Error as error:
            raise PalworldProcessQueryError(
                "Não foi possível inspecionar o processo do Palworld."
            ) from error


class FakePalworldProcessProbe:
    def __init__(self, *, running: bool = False) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running

    def set_running(self, running: bool) -> None:
        self._running = running


def create_palworld_process_probe(settings: Settings) -> PalworldProcessProbe:
    if settings.environment is AppEnvironment.PRODUCTION:
        return SystemdPalworldProcessProbe(settings.palworld_service)
    return FakePalworldProcessProbe()
