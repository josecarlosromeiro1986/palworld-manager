import json
import os
import shutil
import socket
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

import psutil

from app import __version__
from app.config import AppEnvironment, Settings
from app.diagnostics.models import DiagnosticCheck, DiagnosticStatus
from app.system.commands import sanitized_subprocess_environment

WEB_SERVICE_NAME: Final = "palworld-manager.service"
SYSTEMCTL_PATH: Final = "/usr/bin/systemctl"
TAILSCALE_PATH: Final = "/usr/bin/tailscale"
GIT_PATH: Final = "/usr/bin/git"
COMMAND_TIMEOUT_SECONDS: Final = 5.0
MAX_COMMAND_OUTPUT_BYTES: Final = 1024 * 1024


class EnvironmentDiagnosticsProbe(Protocol):
    def checks(self) -> tuple[DiagnosticCheck, ...]: ...


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
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=sanitized_subprocess_environment(),
    )


class FakeEnvironmentDiagnosticsProbe:
    """Fake integral: não consulta serviços, rede ou paths estruturais."""

    def __init__(self, *, commit: str) -> None:
        self._commit = commit

    def checks(self) -> tuple[DiagnosticCheck, ...]:
        return (
            DiagnosticCheck(
                "manager-build",
                "Manager e host",
                "Versão do Manager",
                DiagnosticStatus.OK,
                f"Versão {__version__}; commit {self._commit}.",
            ),
            DiagnosticCheck(
                "web-service",
                "Manager e host",
                "Serviço web e processo",
                DiagnosticStatus.OK,
                "Adapter fake ativo; nenhum serviço do host foi consultado.",
            ),
            DiagnosticCheck(
                "ports",
                "Manager e host",
                "Portas",
                DiagnosticStatus.OK,
                "Portas simuladas coerentes com o ambiente local.",
            ),
            DiagnosticCheck(
                "permissions",
                "Manager e host",
                "Diretórios e permissões",
                DiagnosticStatus.OK,
                "Paths estruturais não são acessados neste ambiente.",
            ),
            DiagnosticCheck(
                "tailscale",
                "Integrações e conectividade",
                "Tailscale e Serve",
                DiagnosticStatus.ATTENTION,
                "Verificação real disponível somente em production.",
            ),
        )


class ProductionEnvironmentDiagnosticsProbe:
    def __init__(
        self,
        settings: Settings,
        *,
        commit: str,
        runner: CommandRunner = _run_command,
        process_inspector: Callable[[int], bool] | None = None,
        port_checker: Callable[[str, int], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._commit = commit
        self._runner = runner
        self._process_inspector = process_inspector or _process_is_running
        self._port_checker = port_checker or _port_is_open

    def checks(self) -> tuple[DiagnosticCheck, ...]:
        return (
            self._build_check(),
            self._web_service_check(),
            self._ports_check(),
            self._permissions_check(),
            self._tailscale_check(),
        )

    def _build_check(self) -> DiagnosticCheck:
        status = DiagnosticStatus.ATTENTION
        if self._commit != "indisponível":
            status = DiagnosticStatus.OK
        return DiagnosticCheck(
            "manager-build",
            "Manager e host",
            "Versão do Manager",
            status,
            f"Versão {__version__}; commit {self._commit}.",
        )

    def _web_service_check(self) -> DiagnosticCheck:
        command = (
            SYSTEMCTL_PATH,
            "show",
            "--property=ActiveState,MainPID",
            WEB_SERVICE_NAME,
        )
        try:
            result = self._runner(command, timeout_seconds=COMMAND_TIMEOUT_SECONDS)
            if result.returncode != 0 or _output_too_large(result.stdout):
                raise ValueError
            properties = _systemd_properties(result.stdout)
            active = properties.get("ActiveState") == "active"
            raw_pid = properties.get("MainPID", "")
            process_running = raw_pid.isdecimal() and int(raw_pid) > 0
            if process_running:
                process_running = self._process_inspector(int(raw_pid))
        except Exception:
            return DiagnosticCheck(
                "web-service",
                "Manager e host",
                "Serviço web e processo",
                DiagnosticStatus.FAILURE,
                "Não foi possível confirmar o serviço web pelo systemd.",
            )
        if active and process_running:
            return DiagnosticCheck(
                "web-service",
                "Manager e host",
                "Serviço web e processo",
                DiagnosticStatus.OK,
                "Serviço ativo e processo principal confirmado.",
            )
        return DiagnosticCheck(
            "web-service",
            "Manager e host",
            "Serviço web e processo",
            DiagnosticStatus.FAILURE,
            "O systemd e o processo principal do serviço web estão inconsistentes.",
        )

    def _ports_check(self) -> DiagnosticCheck:
        rest_url = urlsplit(str(self._settings.palworld_rest_base_url))
        rest_host = rest_url.hostname
        rest_port = rest_url.port
        try:
            web_open = self._port_checker(str(self._settings.app_host), self._settings.app_port)
            rest_open = bool(
                rest_host is not None
                and rest_port is not None
                and self._port_checker(rest_host, rest_port)
            )
        except Exception:
            web_open = False
            rest_open = False
        if not web_open:
            status = DiagnosticStatus.FAILURE
            summary = "A porta local do Manager não respondeu à consulta TCP."
        elif rest_open:
            status = DiagnosticStatus.OK
            summary = "Porta web disponível; porta REST do Palworld acessível."
        else:
            status = DiagnosticStatus.ATTENTION
            summary = "Porta web disponível; porta REST do Palworld está fechada."
        return DiagnosticCheck("ports", "Manager e host", "Portas", status, summary)

    def _permissions_check(self) -> DiagnosticCheck:
        try:
            database = self._settings.manager_database
            settings_file = self._settings.palworld_settings
            directories_ok = (
                _usable_directory(database.parent, writable=True)
                and _usable_directory(self._settings.palworld_dir, writable=False)
                and _usable_directory(settings_file.parent, writable=True)
            )
            files_ok = (
                _usable_file(database, readable=True, writable=True)
                and _usable_file(settings_file, readable=True, writable=True)
                and _usable_file(self._settings.steamcmd, executable=True)
                and _usable_file(self._settings.rclone, executable=True)
            )
        except OSError:
            directories_ok = False
            files_ok = False
        if directories_ok and files_ok:
            return DiagnosticCheck(
                "permissions",
                "Manager e host",
                "Diretórios e permissões",
                DiagnosticStatus.OK,
                "Diretórios, banco, configuração e executáveis exigidos estão acessíveis.",
            )
        return DiagnosticCheck(
            "permissions",
            "Manager e host",
            "Diretórios e permissões",
            DiagnosticStatus.FAILURE,
            "Um ou mais paths estruturais não possuem tipo ou acesso compatível.",
        )

    def _tailscale_check(self) -> DiagnosticCheck:
        try:
            status_payload = self._json_command((TAILSCALE_PATH, "status", "--json"))
            serve_payload = self._json_command((TAILSCALE_PATH, "serve", "status", "--json"))
        except Exception:
            return DiagnosticCheck(
                "tailscale",
                "Integrações e conectividade",
                "Tailscale e Serve",
                DiagnosticStatus.FAILURE,
                "Não foi possível validar Tailscale e Serve com consultas read-only.",
            )
        backend_running = status_payload.get("BackendState") == "Running"
        serve_configured = bool(serve_payload)
        funnel_enabled = _contains_enabled_funnel(serve_payload)
        target = f"{self._settings.app_host}:{self._settings.app_port}"
        target_configured = target in json.dumps(serve_payload, separators=(",", ":"))
        if funnel_enabled:
            status = DiagnosticStatus.FAILURE
            summary = "Tailscale Funnel foi detectado; a V1 exige acesso privado via Serve."
        elif backend_running and serve_configured and target_configured:
            status = DiagnosticStatus.OK
            summary = "Tailscale conectado e Serve aponta para o listener local do Manager."
        elif backend_running:
            status = DiagnosticStatus.ATTENTION
            summary = "Tailscale conectado, mas o destino esperado do Serve não foi confirmado."
        else:
            status = DiagnosticStatus.FAILURE
            summary = "O backend do Tailscale não está conectado."
        return DiagnosticCheck(
            "tailscale",
            "Integrações e conectividade",
            "Tailscale e Serve",
            status,
            summary,
        )

    def _json_command(self, command: Sequence[str]) -> dict[str, object]:
        result = self._runner(command, timeout_seconds=COMMAND_TIMEOUT_SECONDS)
        if result.returncode != 0 or _output_too_large(result.stdout):
            raise ValueError("resposta externa inválida")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("resposta externa inválida")
        return payload


def create_environment_diagnostics_probe(settings: Settings) -> EnvironmentDiagnosticsProbe:
    if settings.environment is AppEnvironment.TEST:
        return FakeEnvironmentDiagnosticsProbe(commit="test")
    commit = resolve_git_commit(settings.environment)
    if settings.environment is AppEnvironment.PRODUCTION:
        return ProductionEnvironmentDiagnosticsProbe(settings, commit=commit)
    return FakeEnvironmentDiagnosticsProbe(commit=commit)


def resolve_git_commit(environment: AppEnvironment) -> str:
    executable = Path(GIT_PATH) if environment is AppEnvironment.PRODUCTION else _local_git_path()
    if executable is None:
        return "indisponível"
    repository = Path(__file__).resolve().parents[2]
    command = [os.fspath(executable)]
    if environment is AppEnvironment.PRODUCTION:
        command.extend(("-c", f"safe.directory={os.fspath(repository)}"))
    command.extend(("-C", os.fspath(repository), "rev-parse", "--short=12", "HEAD"))
    try:
        result = _run_command(
            command,
            timeout_seconds=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "indisponível"
    commit = result.stdout.strip().lower()
    if (
        result.returncode != 0
        or len(commit) != 12
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        return "indisponível"
    return commit


def _local_git_path() -> Path | None:
    discovered = shutil.which("git")
    if discovered is None:
        return None
    path = Path(discovered)
    return path if path.is_absolute() and path.is_file() else None


def _systemd_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ActiveState", "MainPID"}:
            properties[key] = value.strip()
    return properties


def _process_is_running(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _usable_directory(path: Path, *, writable: bool) -> bool:
    if _has_symlink_component(path) or not path.is_dir():
        return False
    mode = os.R_OK | os.X_OK | (os.W_OK if writable else 0)
    return os.access(path, mode)


def _usable_file(
    path: Path,
    *,
    readable: bool = False,
    writable: bool = False,
    executable: bool = False,
) -> bool:
    if _has_symlink_component(path) or not path.is_file():
        return False
    mode = (
        (os.R_OK if readable else 0) | (os.W_OK if writable else 0) | (os.X_OK if executable else 0)
    )
    return os.access(path, mode)


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _output_too_large(output: str) -> bool:
    return len(output.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES


def _contains_enabled_funnel(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            (key.casefold() == "funnel" and nested is True) or _contains_enabled_funnel(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_enabled_funnel(item) for item in value)
    return False
