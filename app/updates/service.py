import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from app.config import AppEnvironment, Settings
from app.system.commands import sanitized_subprocess_environment

PALWORLD_APP_ID: Final = "2394010"
MAX_STEAM_OUTPUT_BYTES: Final = 2 * 1024 * 1024
MAX_MANIFEST_BYTES: Final = 1024 * 1024
STEAM_CHECK_TIMEOUT_SECONDS: Final = 60
STEAM_UPDATE_TIMEOUT_SECONDS: Final = 30 * 60
FAKE_AVAILABLE_AT: Final = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class SteamCmdError(RuntimeError):
    """O SteamCMD ou seus metadados não produziram um resultado confiável."""


@dataclass(frozen=True, slots=True)
class SteamBuildInfo:
    installed_build_id: str
    available_build_id: str
    available_at: datetime | None

    @property
    def update_available(self) -> bool:
        return self.installed_build_id != self.available_build_id


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    output: bytes
    output_truncated: bool = False


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str], *, timeout_seconds: int) -> CommandResult: ...


class SteamCmdGateway(Protocol):
    def check(self) -> SteamBuildInfo: ...

    def apply_update(self) -> None: ...


class DiskSpaceSource(Protocol):
    def free_bytes(self) -> int: ...


def _run_command(command: Sequence[str], *, timeout_seconds: int) -> CommandResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout do SteamCMD deve ser positivo")
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_seconds,
                shell=False,
                env=sanitized_subprocess_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SteamCmdError("o SteamCMD não pôde concluir a operação") from error
        output.seek(0)
        payload = output.read(MAX_STEAM_OUTPUT_BYTES + 1)
    return CommandResult(
        returncode=completed.returncode,
        output=payload[:MAX_STEAM_OUTPUT_BYTES],
        output_truncated=len(payload) > MAX_STEAM_OUTPUT_BYTES,
    )


class FilesystemDiskSpaceSource:
    def __init__(self, managed_path: Path) -> None:
        self._managed_path = _validated_directory(managed_path, "PALWORLD_DIR")

    def free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self._managed_path).free
        except OSError as error:
            raise SteamCmdError("o espaço livre do Palworld não pôde ser consultado") from error


class FakeDiskSpaceSource:
    def __init__(self, available_bytes: int = 100 * 1024**3) -> None:
        self.available_bytes = available_bytes

    def free_bytes(self) -> int:
        return self.available_bytes


class ProductionSteamCmdGateway:
    def __init__(
        self,
        steamcmd: Path,
        palworld_dir: Path,
        *,
        runner: CommandRunner = _run_command,
    ) -> None:
        self._steamcmd = _validated_executable(steamcmd)
        self._palworld_dir = _validated_directory(palworld_dir, "PALWORLD_DIR")
        self._manifest = self._palworld_dir / "steamapps" / f"appmanifest_{PALWORLD_APP_ID}.acf"
        self._runner = runner

    def check(self) -> SteamBuildInfo:
        installed = self._installed_build_id()
        result = self._runner(
            (
                os.fspath(self._steamcmd),
                "+login",
                "anonymous",
                "+app_info_print",
                PALWORLD_APP_ID,
                "+quit",
            ),
            timeout_seconds=STEAM_CHECK_TIMEOUT_SECONDS,
        )
        output = _valid_command_output(result, operation="consulta")
        available, available_at = parse_public_build(output)
        return SteamBuildInfo(installed, available, available_at)

    def apply_update(self) -> None:
        result = self._runner(
            (
                os.fspath(self._steamcmd),
                "+force_install_dir",
                os.fspath(self._palworld_dir),
                "+login",
                "anonymous",
                "+app_update",
                PALWORLD_APP_ID,
                "validate",
                "+quit",
            ),
            timeout_seconds=STEAM_UPDATE_TIMEOUT_SECONDS,
        )
        output = _valid_command_output(result, operation="atualização")
        if f"Success! App '{PALWORLD_APP_ID}' fully installed." not in output:
            raise SteamCmdError("o SteamCMD não confirmou a atualização")

    def _installed_build_id(self) -> str:
        manifest = _validated_regular_file(self._manifest, self._palworld_dir)
        try:
            with manifest.open("rb") as stream:
                payload = stream.read(MAX_MANIFEST_BYTES + 1)
        except OSError as error:
            raise SteamCmdError("o manifesto local do Steam não pôde ser lido") from error
        if len(payload) > MAX_MANIFEST_BYTES:
            raise SteamCmdError("o manifesto local do Steam excede o limite permitido")
        content = _decode_output(payload, "manifesto local")
        parsed = parse_keyvalues(content)
        app_state = parsed.get("AppState")
        if not isinstance(app_state, dict):
            raise SteamCmdError("o manifesto local do Steam é inválido")
        app_id = app_state.get("appid")
        build_id = app_state.get("buildid")
        if app_id != PALWORLD_APP_ID or not _valid_build_id(build_id):
            raise SteamCmdError("o manifesto local do Steam é inválido")
        return cast(str, build_id)


class FakeSteamCmdGateway:
    """Fake integral que não executa SteamCMD nem acessa o filesystem estrutural."""

    def __init__(
        self,
        *,
        installed_build_id: str = "10000001",
        available_build_id: str = "10000002",
        available_at: datetime | None = FAKE_AVAILABLE_AT,
    ) -> None:
        self.installed_build_id = installed_build_id
        self.available_build_id = available_build_id
        self.available_at = available_at
        self.check_error: Exception | None = None
        self.update_error: Exception | None = None
        self.update_calls = 0

    def check(self) -> SteamBuildInfo:
        if self.check_error is not None:
            raise self.check_error
        return SteamBuildInfo(
            self.installed_build_id,
            self.available_build_id,
            self.available_at,
        )

    def apply_update(self) -> None:
        self.update_calls += 1
        if self.update_error is not None:
            raise self.update_error
        self.installed_build_id = self.available_build_id


def create_steamcmd_gateway(settings: Settings) -> SteamCmdGateway:
    if settings.environment is AppEnvironment.PRODUCTION:
        return ProductionSteamCmdGateway(settings.steamcmd, settings.palworld_dir)
    return FakeSteamCmdGateway()


def create_disk_space_source(settings: Settings) -> DiskSpaceSource:
    if settings.environment is AppEnvironment.PRODUCTION:
        return FilesystemDiskSpaceSource(settings.palworld_dir)
    return FakeDiskSpaceSource()


def parse_public_build(content: str) -> tuple[str, datetime | None]:
    parsed = parse_keyvalues(content)
    application = parsed.get(PALWORLD_APP_ID)
    if not isinstance(application, dict):
        raise SteamCmdError("a resposta do SteamCMD não contém o aplicativo esperado")
    depots = application.get("depots")
    branches = depots.get("branches") if isinstance(depots, dict) else None
    public = branches.get("public") if isinstance(branches, dict) else None
    if not isinstance(public, dict):
        raise SteamCmdError("a resposta do SteamCMD não contém a branch pública")
    build_id = public.get("buildid")
    if not _valid_build_id(build_id):
        raise SteamCmdError("a resposta do SteamCMD contém versão inválida")
    raw_timestamp = public.get("timeupdated")
    available_at: datetime | None = None
    if isinstance(raw_timestamp, str) and raw_timestamp.isdecimal():
        try:
            available_at = datetime.fromtimestamp(int(raw_timestamp), tz=UTC)
        except (OverflowError, OSError, ValueError):
            available_at = None
    return cast(str, build_id), available_at


def parse_keyvalues(content: str) -> dict[str, object]:
    tokens = _keyvalue_tokens(content)
    root: dict[str, object] = {}
    index = 0
    while index < len(tokens):
        key = tokens[index]
        if key in {"{", "}"} or index + 1 >= len(tokens):
            index += 1
            continue
        value, index = _parse_keyvalue_value(tokens, index + 1)
        root[key] = value
    return root


def _parse_keyvalue_value(tokens: tuple[str, ...], index: int) -> tuple[object, int]:
    if tokens[index] != "{":
        return tokens[index], index + 1
    result: dict[str, object] = {}
    index += 1
    while index < len(tokens) and tokens[index] != "}":
        key = tokens[index]
        if key == "{":
            raise SteamCmdError("resposta KeyValues inválida")
        if index + 1 >= len(tokens):
            raise SteamCmdError("resposta KeyValues incompleta")
        value, index = _parse_keyvalue_value(tokens, index + 1)
        result[key] = value
    if index >= len(tokens) or tokens[index] != "}":
        raise SteamCmdError("resposta KeyValues incompleta")
    return result, index + 1


def _keyvalue_tokens(content: str) -> tuple[str, ...]:
    tokens: list[str] = []
    index = 0
    while index < len(content):
        character = content[index]
        if character.isspace():
            index += 1
            continue
        if character in "{}":
            tokens.append(character)
            index += 1
            continue
        if character != '"':
            while index < len(content) and content[index] not in '\n\r{}"':
                index += 1
            continue
        index += 1
        value: list[str] = []
        while index < len(content) and content[index] != '"':
            if content[index] == "\\" and index + 1 < len(content):
                index += 1
            value.append(content[index])
            index += 1
        if index >= len(content):
            raise SteamCmdError("resposta KeyValues inválida")
        tokens.append("".join(value))
        index += 1
    return tuple(tokens)


def _valid_command_output(result: CommandResult, *, operation: str) -> str:
    if result.output_truncated:
        raise SteamCmdError(f"a saída da {operation} do SteamCMD excede o limite permitido")
    if result.returncode != 0:
        raise SteamCmdError(f"o SteamCMD falhou durante a {operation}")
    return _decode_output(result.output, f"saída da {operation}")


def _decode_output(payload: bytes, source: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SteamCmdError(f"{source} possui codificação inválida") from error


def _valid_build_id(value: object) -> bool:
    return isinstance(value, str) and value.isdecimal() and 1 <= len(value) <= 20


def _validated_executable(path: Path) -> Path:
    if not path.is_absolute():
        raise SteamCmdError("STEAMCMD deve ser um path absoluto")
    _reject_ambiguous_path(path, "STEAMCMD")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SteamCmdError("STEAMCMD não está disponível") from error
    if path.is_symlink() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SteamCmdError("STEAMCMD não é um executável regular permitido")
    return resolved


def _validated_directory(path: Path, name: str) -> Path:
    if not path.is_absolute():
        raise SteamCmdError(f"{name} deve ser um path absoluto")
    _reject_ambiguous_path(path, name)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SteamCmdError(f"{name} não está disponível") from error
    if path.is_symlink() or not resolved.is_dir():
        raise SteamCmdError(f"{name} não é um diretório regular permitido")
    return resolved


def _validated_regular_file(path: Path, managed_root: Path) -> Path:
    if not path.is_absolute():
        raise SteamCmdError("o manifesto local deve possuir path absoluto")
    try:
        resolved = path.resolve(strict=True)
        root = managed_root.resolve(strict=True)
    except OSError as error:
        raise SteamCmdError("o manifesto local do Steam não está disponível") from error
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise SteamCmdError("o manifesto local escapou da área do Palworld") from error
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SteamCmdError("o manifesto local contém link simbólico")
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise SteamCmdError("o manifesto local do Steam é inválido")
    return resolved


def _reject_ambiguous_path(path: Path, name: str) -> None:
    if path != Path(os.path.normpath(path)):
        raise SteamCmdError(f"{name} possui componentes ambíguos")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise SteamCmdError(f"{name} contém link simbólico")
