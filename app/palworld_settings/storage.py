import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.config import AppEnvironment, Settings

MAX_INI_BYTES = 1024 * 1024

FAKE_PALWORLD_SETTINGS = (
    "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=("
    'ServerName="Servidor de desenvolvimento",'
    'ServerDescription="Ambiente simulado",'
    "ServerPlayerMaxNum=32,RESTAPIEnabled=True,RESTAPIPort=8212,"
    "RCONEnabled=False,RCONPort=25575,ExpRate=1.000000,"
    'PalCaptureRate=1.000000,AdminPassword="valor-fake-nao-exibir",'
    'FutureSetting=(Mode="Preserve,Me"))\n'
)


class SettingsStorageErrorKind(StrEnum):
    CONFLICT = "conflict"
    INVALID_FILE = "invalid_file"
    IO = "io"
    NOT_FOUND = "not_found"
    PERMISSION = "permission"


PUBLIC_STORAGE_MESSAGES = {
    SettingsStorageErrorKind.CONFLICT: (
        "O arquivo foi alterado depois da abertura da página. Recarregue antes de salvar."
    ),
    SettingsStorageErrorKind.INVALID_FILE: (
        "O PalWorldSettings.ini não pôde ser lido ou gravado com segurança."
    ),
    SettingsStorageErrorKind.IO: "Não foi possível acessar o PalWorldSettings.ini.",
    SettingsStorageErrorKind.NOT_FOUND: "O PalWorldSettings.ini não foi encontrado.",
    SettingsStorageErrorKind.PERMISSION: (
        "O Manager não possui permissão para acessar o PalWorldSettings.ini."
    ),
}


class PalworldSettingsStorageError(RuntimeError):
    def __init__(self, kind: SettingsStorageErrorKind) -> None:
        self.kind = kind
        super().__init__(PUBLIC_STORAGE_MESSAGES[kind])

    @property
    def public_message(self) -> str:
        return str(self)


@dataclass(frozen=True, slots=True)
class StoredSettings:
    content: str
    version: str


@dataclass(frozen=True, slots=True)
class SettingsWriteResult:
    backup_name: str


class PalworldSettingsStorage(Protocol):
    def read(self) -> StoredSettings: ...

    def write(self, *, expected_version: str, content: str) -> SettingsWriteResult: ...


def content_version(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FakePalworldSettingsStorage:
    def __init__(self, content: str = FAKE_PALWORLD_SETTINGS) -> None:
        self.content = content
        self.backups: list[tuple[str, str]] = []
        self.error_kind: SettingsStorageErrorKind | None = None

    def set_error(self, kind: SettingsStorageErrorKind | None) -> None:
        self.error_kind = kind

    def read(self) -> StoredSettings:
        self._raise_if_configured()
        encoded = self.content.encode("utf-8")
        return StoredSettings(self.content, content_version(encoded))

    def write(self, *, expected_version: str, content: str) -> SettingsWriteResult:
        self._raise_if_configured()
        current_version = content_version(self.content.encode("utf-8"))
        if current_version != expected_version:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.CONFLICT)
        backup_name = f"PalWorldSettings.ini.backup-{len(self.backups) + 1:04d}"
        self.backups.append((backup_name, self.content))
        self.content = content
        return SettingsWriteResult(backup_name=backup_name)

    def _raise_if_configured(self) -> None:
        if self.error_kind is not None:
            raise PalworldSettingsStorageError(self.error_kind)


class FilePalworldSettingsStorage:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not path.is_absolute():
            raise ValueError("o caminho do PalWorldSettings.ini deve ser absoluto")
        self._path = path
        self._clock = clock

    def read(self) -> StoredSettings:
        content, encoded, _file_stat = self._read_current()
        return StoredSettings(content=content, version=content_version(encoded))

    def write(self, *, expected_version: str, content: str) -> SettingsWriteResult:
        current_content, current_bytes, file_stat = self._read_current()
        del current_content
        if content_version(current_bytes) != expected_version:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.CONFLICT)
        new_bytes = content.encode("utf-8")
        if len(new_bytes) > MAX_INI_BYTES:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.INVALID_FILE)

        timestamp = self._clock().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_name = f"{self._path.name}.backup-{timestamp}-{expected_version[:12]}"
        backup_path = self._path.with_name(backup_name)
        try:
            self._write_exclusive(backup_path, current_bytes, 0o600)
            self._atomic_replace(new_bytes, stat.S_IMODE(file_stat.st_mode))
        except PalworldSettingsStorageError:
            raise
        except PermissionError as error:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.PERMISSION) from error
        except OSError as error:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.IO) from error
        return SettingsWriteResult(backup_name=backup_name)

    def _read_current(self) -> tuple[str, bytes, os.stat_result]:
        try:
            _reject_symlink_components(self._path)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._path, flags)
            with os.fdopen(descriptor, "rb") as file:
                file_stat = os.fstat(file.fileno())
                if not stat.S_ISREG(file_stat.st_mode):
                    raise PalworldSettingsStorageError(SettingsStorageErrorKind.INVALID_FILE)
                encoded = file.read(MAX_INI_BYTES + 1)
        except PalworldSettingsStorageError:
            raise
        except FileNotFoundError as error:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.NOT_FOUND) from error
        except PermissionError as error:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.PERMISSION) from error
        except OSError as error:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.IO) from error
        if len(encoded) > MAX_INI_BYTES:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.INVALID_FILE)
        try:
            content = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PalworldSettingsStorageError(SettingsStorageErrorKind.INVALID_FILE) from error
        return content, encoded, file_stat

    def _write_exclusive(self, path: Path, content: bytes, mode: int) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as file:
            os.fchmod(file.fileno(), mode)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

    def _atomic_replace(self, content: bytes, mode: int) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.palworld-manager-",
            dir=self._path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            _reject_symlink_components(self._path)
            os.replace(temporary_path, self._path)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(self._path.parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise PalworldSettingsStorageError(SettingsStorageErrorKind.INVALID_FILE)
        except FileNotFoundError:
            continue


def create_palworld_settings_storage(settings: Settings) -> PalworldSettingsStorage:
    if settings.environment is AppEnvironment.PRODUCTION:
        return FilePalworldSettingsStorage(settings.palworld_settings)
    return FakePalworldSettingsStorage()
