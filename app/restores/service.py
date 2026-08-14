import json
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

from app.backups.drive_service import DRIVE_TEMPORARY_DIRECTORY_NAME
from app.backups.manifest import (
    MANIFEST_FILENAME,
    BackupValidationError,
    sha256_file,
    validate_archive,
    validate_archive_path,
)
from app.backups.service import LocalBackupService
from app.backups.source import (
    SAFE_MANAGER_SETTING_DEFAULTS,
    SAFE_MANAGER_SETTING_KEYS,
    SENSITIVE_KEY_PATTERN,
    WORLD_RELATIVE_ROOT,
)
from app.config import AppEnvironment, Settings
from app.db.models import BackupRecord
from app.palworld_settings.definitions import (
    SETTING_DEFINITIONS_BY_KEY,
    SettingKind,
)
from app.palworld_settings.ini import (
    IniEntry,
    IniParseError,
    SettingValueError,
    parse_ini,
    parse_setting_value,
)
from app.palworld_settings.storage import (
    MAX_INI_BYTES,
    PalworldSettingsStorage,
    PalworldSettingsStorageError,
    StoredSettings,
    create_palworld_settings_storage,
)

RESTORE_TEMPORARY_DIRECTORY_NAME: Final = "tmp/restores"
MAX_GENERIC_CONFIG_BYTES: Final = 1024 * 1024
MANAGER_PAYLOAD_PATHS: Final = {"manager/manager.db", "manager/settings.json"}
PALWORLD_SETTINGS_PAYLOAD_PATH: Final = "config/PalWorldSettings.ini"
GAME_USER_SETTINGS_PAYLOAD_PATH: Final = "config/GameUserSettings.ini"


class RestoreError(RuntimeError):
    def __init__(self, category: str, public_message: str) -> None:
        self.category = category
        self.public_message = public_message
        super().__init__(public_message)


class RestoreValidationError(RestoreError):
    pass


class RestoreApplyError(RestoreError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedRestore:
    working_directory: Path
    world_directory: Path
    palworld_settings: str
    palworld_settings_version: str
    game_user_settings: str | None
    payload_size_bytes: int


class RestoreTarget(Protocol):
    def read_palworld_settings(self) -> StoredSettings: ...

    def read_game_user_settings(self) -> str | None: ...

    def ensure_available_space(self, required_bytes: int) -> None: ...

    def apply(self, prepared: PreparedRestore, *, job_id: int) -> None: ...


class LocalRestoreService:
    def __init__(
        self,
        *,
        manager_database: Path,
        backup_service: LocalBackupService,
        target: RestoreTarget,
    ) -> None:
        self._data_directory = manager_database.parent.resolve()
        self._temporary_directory = self._data_directory / RESTORE_TEMPORARY_DIRECTORY_NAME
        self._backup_service = backup_service
        self._target = target

    def prepare(self, record: BackupRecord, *, job_id: int) -> PreparedRestore:
        if job_id <= 0:
            raise ValueError("identificador do job de Restore inválido")
        self._validate_record(record, location="LOCAL")
        source = self._backup_service.resolve_managed_artifact(record.storage_path)
        if source is None or not source.is_file() or source.is_symlink():
            raise RestoreValidationError(
                "BACKUP_UNAVAILABLE", "O backup local não está disponível."
            )
        return self._prepare_archive(record, source=source, job_id=job_id)

    def prepare_remote(
        self,
        record: BackupRecord,
        archive_path: Path,
        *,
        job_id: int,
    ) -> PreparedRestore:
        if job_id <= 0:
            raise ValueError("identificador do job de Restore inválido")
        self._validate_record(record, location="DRIVE")
        remote_temporary = self._data_directory / DRIVE_TEMPORARY_DIRECTORY_NAME
        self._validate_downloaded_archive(archive_path, remote_temporary)
        return self._prepare_archive(record, source=archive_path, job_id=job_id)

    def _prepare_archive(
        self,
        record: BackupRecord,
        *,
        source: Path,
        job_id: int,
    ) -> PreparedRestore:

        self._temporary_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._validate_controlled_directory(self._temporary_directory)
        working_directory = Path(
            tempfile.mkdtemp(prefix=f"job-{job_id:06d}-", dir=self._temporary_directory)
        )
        archive_copy = working_directory / "source.tar.gz"
        payload_root = working_directory / "payload"
        payload_root.mkdir(mode=0o700)
        try:
            expected_size = record.size_bytes
            assert expected_size is not None
            self._copy_managed_archive(source, archive_copy, expected_size)
            digest = sha256_file(archive_copy)
            if digest != record.sha256:
                raise RestoreValidationError(
                    "ARCHIVE_SHA256_MISMATCH",
                    "O SHA-256 externo do backup não corresponde ao registro.",
                )
            try:
                manifest = validate_archive(archive_copy)
            except BackupValidationError as error:
                raise RestoreValidationError(
                    "ARCHIVE_INTEGRITY_INVALID", "O backup falhou na validação de integridade."
                ) from error
            self._validate_manifest_identity(manifest, record)
            self._extract_validated_archive(archive_copy, payload_root)
            paths = self._validate_payload(payload_root)
            self._validate_manager_disaster_recovery_payload(payload_root)

            current = self._read_current_settings()
            backup_settings = self._read_payload_text(
                payload_root / PALWORLD_SETTINGS_PAYLOAD_PATH, MAX_INI_BYTES
            )
            merged_settings = merge_palworld_settings(backup_settings, current.content)
            game_user_settings = self._prepare_game_user_settings(payload_root, paths)
            payload_size = sum(
                path.stat().st_size for path in payload_root.rglob("*") if path.is_file()
            )
            self._target.ensure_available_space(payload_size)
            return PreparedRestore(
                working_directory=working_directory,
                world_directory=payload_root / "world",
                palworld_settings=merged_settings,
                palworld_settings_version=current.version,
                game_user_settings=game_user_settings,
                payload_size_bytes=payload_size,
            )
        except Exception:
            shutil.rmtree(working_directory, ignore_errors=True)
            raise

    def apply(self, prepared: PreparedRestore, *, job_id: int) -> None:
        self._target.apply(prepared, job_id=job_id)

    @staticmethod
    def cleanup(prepared: PreparedRestore | None) -> None:
        if prepared is not None:
            shutil.rmtree(prepared.working_directory, ignore_errors=True)

    def _validate_record(self, record: BackupRecord, *, location: str) -> None:
        if (
            record.location != location
            or record.status != "VALID"
            or record.sha256 is None
            or len(record.sha256) != 64
            or record.size_bytes is None
            or record.size_bytes <= 0
            or Path(record.storage_path).name != record.filename
            or any(character not in "0123456789abcdef" for character in record.sha256)
            or (
                record.storage_path != record.filename
                if location == "DRIVE"
                else Path(record.storage_path).parent.as_posix() != "backups"
            )
        ):
            raise RestoreValidationError(
                "BACKUP_RECORD_INVALID",
                "O registro selecionado não representa um backup válido.",
            )

    def _validate_downloaded_archive(self, archive_path: Path, temporary_root: Path) -> None:
        try:
            self._validate_controlled_directory(temporary_root)
            relative = archive_path.relative_to(temporary_root)
        except (OSError, ValueError, RestoreValidationError) as error:
            raise RestoreValidationError(
                "REMOTE_TEMP_INVALID", "O download temporário do backup é inválido."
            ) from error
        current = temporary_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise RestoreValidationError(
                    "REMOTE_TEMP_INVALID", "O download temporário do backup é inválido."
                )
        if (
            archive_path.is_symlink()
            or not archive_path.is_file()
            or not archive_path.resolve().is_relative_to(temporary_root.resolve())
        ):
            raise RestoreValidationError(
                "REMOTE_TEMP_INVALID", "O download temporário do backup é inválido."
            )

    def _validate_controlled_directory(self, directory: Path) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise RestoreValidationError(
                "RESTORE_TEMP_INVALID", "A área temporária de Restore é inválida."
            )
        if not directory.resolve().is_relative_to(self._data_directory):
            raise RestoreValidationError(
                "RESTORE_TEMP_INVALID", "A área temporária de Restore é inválida."
            )
        current = self._data_directory
        for part in directory.relative_to(self._data_directory).parts:
            current /= part
            if current.is_symlink():
                raise RestoreValidationError(
                    "RESTORE_TEMP_INVALID", "A área temporária de Restore é inválida."
                )

    @staticmethod
    def _copy_managed_archive(source: Path, target: Path, expected_size: int) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(source, flags)
            with os.fdopen(descriptor, "rb") as input_stream, target.open("xb") as output_stream:
                source_stat = os.fstat(input_stream.fileno())
                if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size != expected_size:
                    raise RestoreValidationError(
                        "ARCHIVE_SIZE_MISMATCH", "O tamanho do backup não corresponde ao registro."
                    )
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        except RestoreError:
            raise
        except (OSError, PermissionError) as error:
            raise RestoreValidationError(
                "BACKUP_UNAVAILABLE", "O backup local não pôde ser lido com segurança."
            ) from error
        if target.stat().st_size != expected_size:
            raise RestoreValidationError(
                "ARCHIVE_SIZE_MISMATCH", "O tamanho do backup não corresponde ao registro."
            )

    @staticmethod
    def _validate_manifest_identity(manifest: dict[str, object], record: BackupRecord) -> None:
        backup_id = manifest.get("backup_id")
        if not isinstance(backup_id, str) or not record.filename.endswith(f"-{backup_id}.tar.gz"):
            raise RestoreValidationError(
                "MANIFEST_ID_MISMATCH", "O identificador do manifest não corresponde ao backup."
            )

    @staticmethod
    def _extract_validated_archive(archive_path: Path, payload_root: Path) -> None:
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                for member in archive.getmembers():
                    if member.name == MANIFEST_FILENAME:
                        continue
                    validate_archive_path(member.name)
                    if not member.isfile():
                        raise RestoreValidationError(
                            "ARCHIVE_ENTRY_INVALID", "O backup contém uma entrada insegura."
                        )
                    target = payload_root.joinpath(*PurePosixPath(member.name).parts)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if not target.parent.resolve().is_relative_to(payload_root.resolve()):
                        raise RestoreValidationError(
                            "ARCHIVE_PATH_INVALID", "O backup contém um path inseguro."
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise RestoreValidationError(
                            "ARCHIVE_ENTRY_INVALID", "O backup contém uma entrada ilegível."
                        )
                    with target.open("xb") as output:
                        shutil.copyfileobj(stream, output, length=1024 * 1024)
                    os.chmod(target, 0o600)
        except RestoreError:
            raise
        except (OSError, tarfile.TarError) as error:
            raise RestoreValidationError(
                "ARCHIVE_EXTRACTION_FAILED", "O backup não pôde ser preparado com segurança."
            ) from error

    @staticmethod
    def _validate_payload(payload_root: Path) -> set[str]:
        files = tuple(path for path in payload_root.rglob("*") if path.is_file())
        paths = {path.relative_to(payload_root).as_posix() for path in files}
        if not paths or any(
            not (
                path.startswith("world/")
                or path.startswith("config/")
                or path.startswith("manager/")
            )
            for path in paths
        ):
            raise RestoreValidationError("PAYLOAD_SCOPE_INVALID", "O escopo do payload é inválido.")
        allowed_configs = {
            PALWORLD_SETTINGS_PAYLOAD_PATH,
            GAME_USER_SETTINGS_PAYLOAD_PATH,
        }
        if any(path.startswith("config/") and path not in allowed_configs for path in paths):
            raise RestoreValidationError(
                "PAYLOAD_SCOPE_INVALID", "O escopo das configurações é inválido."
            )
        if any(path.startswith("manager/") and path not in MANAGER_PAYLOAD_PATHS for path in paths):
            raise RestoreValidationError(
                "PAYLOAD_SCOPE_INVALID", "O escopo de disaster recovery é inválido."
            )
        if not MANAGER_PAYLOAD_PATHS.issubset(paths) or PALWORLD_SETTINGS_PAYLOAD_PATH not in paths:
            raise RestoreValidationError(
                "PAYLOAD_INCOMPLETE", "O backup não contém todos os arquivos obrigatórios."
            )
        world_paths = {path.removeprefix("world/") for path in paths if path.startswith("world/")}
        level_parents = {
            str(PurePosixPath(path).parent)
            for path in world_paths
            if PurePosixPath(path).name == "Level.sav"
        }
        if not level_parents or not any(
            f"{parent}/LevelMeta.sav" in world_paths
            and any(item.startswith(f"{parent}/Players/") for item in world_paths)
            for parent in level_parents
        ):
            raise RestoreValidationError(
                "WORLD_INCOMPLETE", "O backup não contém um mundo completo."
            )
        return paths

    @staticmethod
    def _validate_manager_disaster_recovery_payload(payload_root: Path) -> None:
        database = payload_root / "manager/manager.db"
        try:
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            try:
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RestoreValidationError(
                        "MANAGER_DATABASE_INVALID",
                        "A cópia de disaster recovery do Manager é inválida.",
                    )
            finally:
                connection.close()
        except RestoreError:
            raise
        except sqlite3.Error as error:
            raise RestoreValidationError(
                "MANAGER_DATABASE_INVALID", "A cópia de disaster recovery do Manager é inválida."
            ) from error

        settings_path = payload_root / "manager/settings.json"
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RestoreValidationError(
                "MANAGER_SETTINGS_INVALID",
                "A cópia de disaster recovery das configurações é inválida.",
            ) from error
        if not isinstance(raw, dict) or set(raw) != SAFE_MANAGER_SETTING_KEYS:
            raise RestoreValidationError(
                "MANAGER_SETTINGS_INVALID",
                "A cópia de disaster recovery das configurações é inválida.",
            )
        if any(
            type(raw[key]) is not type(default)
            for key, default in SAFE_MANAGER_SETTING_DEFAULTS.items()
        ):
            raise RestoreValidationError(
                "MANAGER_SETTINGS_INVALID",
                "A cópia de disaster recovery das configurações é inválida.",
            )

    def _read_current_settings(self) -> StoredSettings:
        try:
            return self._target.read_palworld_settings()
        except PalworldSettingsStorageError as error:
            raise RestoreValidationError(
                "CURRENT_SETTINGS_INVALID",
                "O PalWorldSettings.ini atual não pode ser combinado com segurança.",
            ) from error

    def _prepare_game_user_settings(self, payload_root: Path, paths: set[str]) -> str | None:
        if GAME_USER_SETTINGS_PAYLOAD_PATH not in paths:
            return None
        backup = self._read_payload_text(
            payload_root / GAME_USER_SETTINGS_PAYLOAD_PATH, MAX_GENERIC_CONFIG_BYTES
        )
        try:
            current = self._target.read_game_user_settings()
        except (OSError, UnicodeDecodeError) as error:
            raise RestoreValidationError(
                "CURRENT_RELATED_SETTINGS_INVALID",
                "A configuração relacionada atual não pode ser combinada com segurança.",
            ) from error
        if current is None:
            raise RestoreValidationError(
                "CURRENT_RELATED_SETTINGS_MISSING",
                "A configuração relacionada atual não foi encontrada.",
            )
        return merge_generic_ini(backup, current)

    @staticmethod
    def _read_payload_text(path: Path, maximum: int) -> str:
        try:
            encoded = path.read_bytes()
        except OSError as error:
            raise RestoreValidationError(
                "PAYLOAD_CONFIG_INVALID", "Uma configuração do backup é ilegível."
            ) from error
        if len(encoded) > maximum:
            raise RestoreValidationError(
                "PAYLOAD_CONFIG_INVALID", "Uma configuração do backup excede o limite."
            )
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RestoreValidationError(
                "PAYLOAD_CONFIG_INVALID", "Uma configuração do backup é inválida."
            ) from error


def merge_palworld_settings(backup_content: str, current_content: str) -> str:
    try:
        backup = parse_ini(backup_content)
        current = parse_ini(current_content)
        backup_entries = _unique_ini_entries(backup.entries)
        current_entries = _unique_ini_entries(current.entries)
        _validate_known_values(current_entries)
        _validate_known_values(backup_entries)
        replacements: dict[str, str] = {}
        for key, entry in backup_entries.items():
            definition = SETTING_DEFINITIONS_BY_KEY.get(key)
            if definition is None or definition.kind is SettingKind.SENSITIVE:
                continue
            if key not in current_entries or entry.value is None:
                raise RestoreValidationError(
                    "SETTINGS_MERGE_UNSAFE",
                    "O PalWorldSettings.ini não pode ser combinado de forma determinística.",
                )
            replacements[key] = entry.value
        merged = current.render(replacements)
        merged_entries = _unique_ini_entries(parse_ini(merged).entries)
        _validate_known_values(merged_entries)
        for key, entry in current_entries.items():
            definition = SETTING_DEFINITIONS_BY_KEY.get(key)
            sensitive = (
                definition is not None and definition.kind is SettingKind.SENSITIVE
            ) or SENSITIVE_KEY_PATTERN.search(key) is not None
            if sensitive and (
                merged_entries.get(key) is None or merged_entries[key].value != entry.value
            ):
                raise RestoreValidationError(
                    "SETTINGS_SECRET_CHANGED", "Um valor sensível não pôde ser preservado."
                )
        return merged
    except RestoreError:
        raise
    except (IniParseError, SettingValueError) as error:
        raise RestoreValidationError(
            "SETTINGS_MERGE_UNSAFE", "O PalWorldSettings.ini não pode ser combinado com segurança."
        ) from error


def _unique_ini_entries(entries: tuple[IniEntry, ...]) -> dict[str, IniEntry]:
    result: dict[str, IniEntry] = {}
    for entry in entries:
        key = entry.key
        if key is None:
            continue
        if key in result:
            raise RestoreValidationError(
                "SETTINGS_MERGE_UNSAFE", "O PalWorldSettings.ini contém parâmetros duplicados."
            )
        result[key] = entry
    return result


def _validate_known_values(entries: dict[str, IniEntry]) -> None:
    for key, entry in entries.items():
        definition = SETTING_DEFINITIONS_BY_KEY.get(key)
        if definition is None or definition.kind is SettingKind.READ_ONLY:
            continue
        value = entry.value
        if value is None:
            raise IniParseError("entrada conhecida não possui valor")
        if definition.kind is SettingKind.SENSITIVE:
            continue
        parse_setting_value(definition, value)


def merge_generic_ini(backup_content: str, current_content: str) -> str:
    backup_lines = backup_content.splitlines(keepends=True)
    current_values = _generic_sensitive_values(current_content)
    seen: set[tuple[str, str]] = set()
    section = ""
    result: list[str] = []
    for line in backup_lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            result.append(line)
            continue
        key, separator, _value = line.partition("=")
        normalized = key.strip()
        if separator and SENSITIVE_KEY_PATTERN.search(normalized) is not None:
            identity = (section.casefold(), normalized.casefold())
            if identity in seen or identity not in current_values:
                raise RestoreValidationError(
                    "RELATED_SETTINGS_MERGE_UNSAFE",
                    "A configuração relacionada não pode ser combinada com segurança.",
                )
            seen.add(identity)
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            result.append(f"{key}={current_values[identity]}{ending}")
        else:
            result.append(line)
    merged = "".join(result)
    if len(merged.encode("utf-8")) > MAX_GENERIC_CONFIG_BYTES:
        raise RestoreValidationError(
            "RELATED_SETTINGS_MERGE_UNSAFE", "A configuração relacionada é inválida."
        )
    return merged


def _generic_sensitive_values(content: str) -> dict[tuple[str, str], str]:
    if len(content.encode("utf-8")) > MAX_GENERIC_CONFIG_BYTES:
        raise RestoreValidationError(
            "CURRENT_RELATED_SETTINGS_INVALID", "A configuração relacionada é inválida."
        )
    result: dict[tuple[str, str], str] = {}
    section = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        key, separator, value = line.partition("=")
        normalized = key.strip()
        if separator and SENSITIVE_KEY_PATTERN.search(normalized) is not None:
            identity = (section.casefold(), normalized.casefold())
            if identity in result or not value.strip():
                raise RestoreValidationError(
                    "CURRENT_RELATED_SETTINGS_INVALID",
                    "A configuração relacionada atual é inválida.",
                )
            result[identity] = value
    return result


class FakeRestoreTarget:
    """Fake integral: não lê nem modifica o filesystem estrutural do Palworld."""

    def __init__(self, storage: PalworldSettingsStorage) -> None:
        self.storage = storage
        self.world_files: dict[str, bytes] = {"old/Level.sav": b"current-world"}
        self.game_user_settings: str | None = None
        self.available_bytes = 1024 * 1024 * 1024
        self.apply_error: RestoreApplyError | None = None
        self.apply_calls = 0

    def read_palworld_settings(self) -> StoredSettings:
        return self.storage.read()

    def read_game_user_settings(self) -> str | None:
        return self.game_user_settings

    def ensure_available_space(self, required_bytes: int) -> None:
        if required_bytes > self.available_bytes:
            raise RestoreValidationError(
                "DISK_SPACE_INSUFFICIENT", "Não há espaço livre suficiente para o Restore."
            )

    def apply(self, prepared: PreparedRestore, *, job_id: int) -> None:
        del job_id
        self.apply_calls += 1
        if self.apply_error is not None:
            raise self.apply_error
        restored: dict[str, bytes] = {}
        for path in prepared.world_directory.rglob("*"):
            if path.is_file():
                restored[path.relative_to(prepared.world_directory).as_posix()] = path.read_bytes()
        self.world_files = restored
        self.storage.write(
            expected_version=prepared.palworld_settings_version,
            content=prepared.palworld_settings,
        )
        self.game_user_settings = prepared.game_user_settings


class FilesystemRestoreTarget:
    def __init__(self, settings: Settings, storage: PalworldSettingsStorage) -> None:
        self._palworld_root = settings.palworld_dir
        self._world_root = self._palworld_root / WORLD_RELATIVE_ROOT
        self._settings_path = settings.palworld_settings
        self._game_user_settings_path = settings.palworld_settings.with_name("GameUserSettings.ini")
        self._storage = storage

    def read_palworld_settings(self) -> StoredSettings:
        try:
            _reject_symlink_components(self._settings_path, self._palworld_root)
        except OSError as error:
            raise RestoreValidationError(
                "CURRENT_SETTINGS_INVALID",
                "O PalWorldSettings.ini atual não está em uma área estrutural segura.",
            ) from error
        return self._storage.read()

    def read_game_user_settings(self) -> str | None:
        path = self._game_user_settings_path
        if not path.exists():
            return None
        return _read_safe_file(path, self._palworld_root, MAX_GENERIC_CONFIG_BYTES).decode("utf-8")

    def ensure_available_space(self, required_bytes: int) -> None:
        _validate_directory(self._world_root, self._palworld_root)
        free = shutil.disk_usage(self._world_root.parent).free
        if required_bytes > free:
            raise RestoreValidationError(
                "DISK_SPACE_INSUFFICIENT", "Não há espaço livre suficiente para o Restore."
            )

    def apply(self, prepared: PreparedRestore, *, job_id: int) -> None:
        _validate_directory(self._world_root, self._palworld_root)
        parent = self._world_root.parent
        new_root = parent / f".SaveGames.palworld-manager-restore-j{job_id:06d}-new"
        previous_root = parent / f".SaveGames.palworld-manager-restore-j{job_id:06d}-previous"
        if (
            new_root.exists()
            or new_root.is_symlink()
            or previous_root.exists()
            or previous_root.is_symlink()
        ):
            raise RestoreApplyError(
                "RESTORE_STATE_AMBIGUOUS", "Existem artefatos de Restore que exigem revisão manual."
            )
        root_stat = self._world_root.stat()
        try:
            _copy_world_tree(prepared.world_directory, new_root, root_stat.st_gid)
            os.replace(self._world_root, previous_root)
            os.replace(new_root, self._world_root)
            self._storage.write(
                expected_version=prepared.palworld_settings_version,
                content=prepared.palworld_settings,
            )
            if prepared.game_user_settings is not None:
                _replace_related_settings(
                    self._game_user_settings_path,
                    prepared.game_user_settings,
                    self._palworld_root,
                )
            shutil.rmtree(previous_root)
        except RestoreError:
            raise
        except PalworldSettingsStorageError as error:
            raise RestoreApplyError(
                "SETTINGS_APPLY_FAILED", "A configuração combinada não pôde ser aplicada."
            ) from error
        except (OSError, PermissionError) as error:
            raise RestoreApplyError(
                "WORLD_APPLY_FAILED", "O mundo não pôde ser restaurado com segurança."
            ) from error


def create_restore_target(settings: Settings) -> RestoreTarget:
    storage = create_palworld_settings_storage(settings)
    if settings.environment is AppEnvironment.PRODUCTION:
        return FilesystemRestoreTarget(settings, storage)
    return FakeRestoreTarget(storage)


def _validate_directory(path: Path, managed_root: Path) -> None:
    try:
        _reject_symlink_components(path, managed_root)
    except OSError as error:
        raise RestoreValidationError(
            "RESTORE_TARGET_INVALID", "O diretório estrutural do mundo é inválido."
        ) from error
    if (
        managed_root.is_symlink()
        or not managed_root.is_dir()
        or path.is_symlink()
        or not path.is_dir()
        or not path.resolve().is_relative_to(managed_root.resolve())
    ):
        raise RestoreValidationError(
            "RESTORE_TARGET_INVALID", "O diretório estrutural do mundo é inválido."
        )
    current = managed_root
    for part in path.relative_to(managed_root).parts:
        current /= part
        if current.is_symlink():
            raise RestoreValidationError(
                "RESTORE_TARGET_INVALID", "O diretório estrutural do mundo é inválido."
            )


def _read_safe_file(path: Path, managed_root: Path, maximum: int) -> bytes:
    _reject_symlink_components(path, managed_root)
    if not path.resolve().is_relative_to(managed_root.resolve()):
        raise OSError("path externo")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("arquivo não regular")
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise OSError("arquivo excede limite")
    return content


def _copy_world_tree(source: Path, target: Path, group_id: int) -> None:
    target.mkdir(mode=0o770)
    os.chown(target, -1, group_id)
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise RestoreApplyError(
                "WORLD_SOURCE_INVALID", "O mundo preparado contém link simbólico."
            )
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(mode=0o770)
            os.chmod(destination, 0o770)
            os.chown(destination, -1, group_id)
        elif path.is_file():
            destination.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
            with path.open("rb") as input_stream, destination.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            os.chmod(destination, 0o660)
            os.chown(destination, -1, group_id)
        else:
            raise RestoreApplyError(
                "WORLD_SOURCE_INVALID", "O mundo preparado contém entrada inválida."
            )


def _replace_related_settings(path: Path, content: str, managed_root: Path) -> None:
    _reject_symlink_components(path, managed_root)
    current_stat = path.stat()
    if path.is_symlink() or not stat.S_ISREG(current_stat.st_mode):
        raise OSError("configuração relacionada inválida")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.palworld-manager-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IMODE(current_stat.st_mode))
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _reject_symlink_components(path: Path, managed_root: Path) -> None:
    if not path.is_absolute() or not managed_root.is_absolute():
        raise OSError("área administrada inválida")
    try:
        resolved_root = managed_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except ValueError as error:
        raise OSError("path externo") from error
    if not resolved_path.is_relative_to(resolved_root):
        raise OSError("path externo")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise OSError("path contém link simbólico")
