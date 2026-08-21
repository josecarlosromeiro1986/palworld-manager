import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppEnvironment, Settings
from app.db.engine import session_scope
from app.db.models import AppSetting
from app.integrations.palworld_rest import PalworldRestClient
from app.manager_settings.service import DEFAULT_MANAGER_SETTINGS, OPERATIONAL_SETTING_KEYS
from app.palworld_settings.definitions import SETTING_DEFINITIONS, SettingKind
from app.palworld_settings.ini import parse_ini

WORLD_RELATIVE_ROOT: Final = Path("Pal/Saved/SaveGames")
PROHIBITED_DIRECTORY_NAMES: Final = {"backup", "backups"}
PROHIBITED_FILE_NAMES: Final = {"secrets.env"}
SAFE_MANAGER_SETTING_KEYS: Final = OPERATIONAL_SETTING_KEYS
SAFE_MANAGER_SETTING_DEFAULTS: Final[dict[str, object]] = dict(DEFAULT_MANAGER_SETTINGS)
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|cookie|webhook|credential|api[_-]?key)"
)
MAX_CONFIG_BYTES: Final = 1024 * 1024


class BackupSourceError(RuntimeError):
    """A fonte estrutural do backup é inválida ou indisponível."""


class BackupPayloadSource(Protocol):
    def request_safe_save(self) -> None: ...

    def stage_palworld_payload(self, payload_root: Path) -> None: ...


class FilesystemBackupPayloadSource:
    def __init__(self, settings: Settings, rest_client: PalworldRestClient) -> None:
        self._palworld_root = settings.palworld_dir
        self._settings_path = settings.palworld_settings
        self._rest_client = rest_client

    def request_safe_save(self) -> None:
        self._rest_client.save_world()

    def stage_palworld_payload(self, payload_root: Path) -> None:
        world_root = self._palworld_root / WORLD_RELATIVE_ROOT
        _validate_regular_directory(world_root, self._palworld_root)
        world_target = payload_root / "world"
        world_target.mkdir(mode=0o700)
        copied_names: set[str] = set()
        for source in _iter_safe_files(world_root):
            relative = source.relative_to(world_root)
            if any(part.casefold() in PROHIBITED_DIRECTORY_NAMES for part in relative.parts):
                continue
            if source.name.casefold() in PROHIBITED_FILE_NAMES:
                continue
            target = world_target / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _copy_regular_file(source, target)
            copied_names.add(source.name)
        required = {"Level.sav", "LevelMeta.sav"}
        if not required.issubset(copied_names) or not any(
            path.is_dir() and path.name == "Players" for path in world_root.rglob("Players")
        ):
            raise BackupSourceError("o mundo não contém os dados persistentes obrigatórios")

        _validate_regular_file(self._settings_path, self._palworld_root)
        config_target = payload_root / "config"
        config_target.mkdir(mode=0o700)
        content = _read_regular_text(self._settings_path)
        (config_target / "PalWorldSettings.ini").write_text(
            _redact_palworld_settings(content),
            encoding="utf-8",
        )
        related = self._settings_path.with_name("GameUserSettings.ini")
        if related.exists():
            _validate_regular_file(related, self._palworld_root)
            (config_target / related.name).write_text(
                _redact_generic_ini(_read_regular_text(related)),
                encoding="utf-8",
            )


class FakeBackupPayloadSource:
    """Fake completo que não acessa os paths estruturais do Palworld."""

    def __init__(self, rest_client: PalworldRestClient) -> None:
        self._rest_client = rest_client

    def request_safe_save(self) -> None:
        self._rest_client.save_world()

    def stage_palworld_payload(self, payload_root: Path) -> None:
        world = payload_root / "world" / "00000000000000000000000000000000"
        players = world / "Players"
        players.mkdir(mode=0o700, parents=True)
        (world / "Level.sav").write_bytes(b"fake-level-save")
        (world / "LevelMeta.sav").write_bytes(b"fake-level-meta")
        (players / "00000000000000000000000000000001.sav").write_bytes(b"fake-player")
        config = payload_root / "config"
        config.mkdir(mode=0o700)
        (config / "PalWorldSettings.ini").write_text(
            '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName="Fake",'
            'AdminPassword="",ServerPassword="")\n',
            encoding="utf-8",
        )


def create_backup_payload_source(
    settings: Settings,
    rest_client: PalworldRestClient,
) -> BackupPayloadSource:
    if settings.environment is AppEnvironment.PRODUCTION:
        return FilesystemBackupPayloadSource(settings, rest_client)
    return FakeBackupPayloadSource(rest_client)


def stage_manager_payload(
    payload_root: Path,
    database_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    manager = payload_root / "manager"
    manager.mkdir(mode=0o700)
    snapshot_database(database_path, manager / "manager.db")
    with session_scope(session_factory) as session:
        overrides = {
            item.key: item.value
            for item in session.scalars(
                select(AppSetting).where(AppSetting.key.in_(SAFE_MANAGER_SETTING_KEYS))
            )
        }
    settings = {**SAFE_MANAGER_SETTING_DEFAULTS, **overrides}
    (manager / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def snapshot_database(source: Path, target: Path) -> None:
    _validate_regular_file(source, source.parent)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        result = target_connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise BackupSourceError("a cópia consistente do SQLite falhou na integridade")
    except sqlite3.Error as error:
        raise BackupSourceError("não foi possível copiar o banco do Manager") from error
    finally:
        target_connection.close()
        source_connection.close()


def _redact_palworld_settings(content: str) -> str:
    parsed = parse_ini(content)
    sensitive = {
        definition.key
        for definition in SETTING_DEFINITIONS
        if definition.kind is SettingKind.SENSITIVE
    }
    replacements = {
        entry.key: '""'
        for entry in parsed.entries
        if entry.key is not None
        and (entry.key in sensitive or SENSITIVE_KEY_PATTERN.search(entry.key) is not None)
    }
    return parsed.render(replacements)


def _redact_generic_ini(content: str) -> str:
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        key, separator, _value = line.partition("=")
        if separator and SENSITIVE_KEY_PATTERN.search(key.strip()) is not None:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines.append(f'{key}=""{ending}')
        else:
            lines.append(line)
    return "".join(lines)


def _iter_safe_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in tuple(directories):
            directory = current_path / name
            if directory.is_symlink():
                raise BackupSourceError("o mundo contém link simbólico")
        for name in filenames:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BackupSourceError("o mundo contém entrada não regular")
            if not path.resolve().is_relative_to(root.resolve()):
                raise BackupSourceError("arquivo do mundo escapou da área administrada")
            files.append(path)
    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def _copy_regular_file(source: Path, target: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as input_stream, target.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)


def _read_regular_text(source: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(MAX_CONFIG_BYTES + 1)
    if len(content) > MAX_CONFIG_BYTES:
        raise BackupSourceError("configuração excede o limite permitido")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BackupSourceError("configuração possui codificação inválida") from error


def _validate_regular_directory(path: Path, managed_root: Path) -> None:
    _validate_within(path, managed_root)
    if path.is_symlink() or not path.is_dir():
        raise BackupSourceError("diretório estrutural do backup é inválido")


def _validate_regular_file(path: Path, managed_root: Path) -> None:
    _validate_within(path, managed_root)
    if path.is_symlink() or not path.is_file():
        raise BackupSourceError("arquivo estrutural do backup é inválido")


def _validate_within(path: Path, managed_root: Path) -> None:
    if not path.is_absolute() or not managed_root.is_absolute():
        raise BackupSourceError("path estrutural deve ser absoluto")
    try:
        resolved_root = managed_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BackupSourceError("path estrutural não está disponível") from error
    if not resolved.is_relative_to(resolved_root):
        raise BackupSourceError("path estrutural escapou da área administrada")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise BackupSourceError("path estrutural contém link simbólico")
