import io
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.backups.manifest import (
    MANIFEST_FILENAME,
    BackupValidationError,
    ManifestFile,
    build_manifest,
    payload_manifest_files,
    sha256_file,
    validate_archive,
)
from app.backups.source import BackupPayloadSource, stage_manager_payload

BACKUP_FILENAME_PATTERN: Final = re.compile(
    r"^palworld-manager-backup-\d{8}T\d{12}Z-j\d{6,}-[0-9a-f]{32}\.tar\.gz$"
)
BACKUP_DIRECTORY_NAME: Final = "backups"
TEMPORARY_DIRECTORY_NAME: Final = "tmp/backups"


class BackupCancelledError(RuntimeError):
    """O backup foi cancelado em um ponto seguro."""


ProgressCallback = Callable[[str, int, bool], None]


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    backup_id: str
    filename: str
    storage_path: str
    sha256: str
    size_bytes: int
    created_at: datetime


class LocalBackupService:
    def __init__(
        self,
        *,
        manager_database: Path,
        session_factory: sessionmaker[Session],
        payload_source: BackupPayloadSource,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        identifier_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._manager_database = manager_database
        self._data_directory = manager_database.parent.resolve()
        self._backup_directory = self._data_directory / BACKUP_DIRECTORY_NAME
        self._temporary_directory = self._data_directory / TEMPORARY_DIRECTORY_NAME
        self._session_factory = session_factory
        self._payload_source = payload_source
        self._clock = clock
        self._identifier_factory = identifier_factory

    def create(
        self,
        *,
        job_id: int,
        trigger: str,
        progress: ProgressCallback,
    ) -> BackupArtifact:
        if job_id <= 0:
            raise ValueError("identificador do job de backup inválido")
        created_at = self._clock().astimezone(UTC)
        backup_id = self._identifier_factory().hex
        filename = (
            f"palworld-manager-backup-{created_at.strftime('%Y%m%dT%H%M%S%fZ')}"
            f"-j{job_id:06d}-{backup_id}.tar.gz"
        )
        if BACKUP_FILENAME_PATTERN.fullmatch(filename) is None:
            raise AssertionError("nome interno de backup inválido")
        self._backup_directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        self._temporary_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._validate_controlled_directory(self._backup_directory)
        self._validate_controlled_directory(self._temporary_directory)
        working_directory = Path(
            tempfile.mkdtemp(
                prefix=f"job-{job_id:06d}-{backup_id}-",
                dir=self._temporary_directory,
            )
        )
        payload_root = working_directory / "payload"
        payload_root.mkdir(mode=0o700)
        partial_archive = working_directory / f"{filename}.partial"
        final_archive = self._backup_directory / filename
        published = False
        try:
            progress("SAFE_SAVE", 10, True)
            self._payload_source.request_safe_save()
            progress("COPYING_WORLD", 25, True)
            self._payload_source.stage_palworld_payload(payload_root)
            progress("COPYING_DATABASE", 45, True)
            stage_manager_payload(
                payload_root,
                self._manager_database,
                self._session_factory,
            )
            progress("BUILDING_MANIFEST", 60, True)
            files = payload_manifest_files(payload_root)
            manifest = build_manifest(
                backup_id=backup_id,
                created_at_utc=created_at.isoformat().replace("+00:00", "Z"),
                trigger=trigger,
                files=files,
            )
            progress("COMPRESSING", 70, True)
            self._write_archive(partial_archive, payload_root, files, manifest, created_at)
            progress("VALIDATING", 85, True)
            parsed_manifest = validate_archive(partial_archive)
            if parsed_manifest.get("backup_id") != backup_id:
                raise BackupValidationError("identificador do manifest não corresponde ao backup")
            digest = sha256_file(partial_archive)
            size_bytes = partial_archive.stat().st_size
            progress("PUBLISHING", 95, False)
            if final_archive.exists() or final_archive.is_symlink():
                raise BackupValidationError("o destino final do backup já existe")
            os.replace(partial_archive, final_archive)
            os.chmod(final_archive, 0o640)
            published = True
            validate_archive(final_archive)
            if sha256_file(final_archive) != digest:
                raise BackupValidationError("o hash do backup mudou durante a publicação")
            return BackupArtifact(
                backup_id=backup_id,
                filename=filename,
                storage_path=f"{BACKUP_DIRECTORY_NAME}/{filename}",
                sha256=digest,
                size_bytes=size_bytes,
                created_at=created_at,
            )
        except Exception:
            if published:
                self.remove_managed_artifact(f"{BACKUP_DIRECTORY_NAME}/{filename}")
            raise
        finally:
            shutil.rmtree(working_directory, ignore_errors=True)

    def remove_managed_artifact(self, storage_path: str) -> bool:
        target = self.resolve_managed_artifact(storage_path)
        if target is None or not target.exists() or target.is_symlink() or not target.is_file():
            return False
        target.unlink()
        return True

    def resolve_managed_artifact(self, storage_path: str) -> Path | None:
        relative = Path(storage_path)
        if relative.is_absolute() or len(relative.parts) != 2:
            return None
        directory, filename = relative.parts
        if (
            directory != BACKUP_DIRECTORY_NAME
            or BACKUP_FILENAME_PATTERN.fullmatch(filename) is None
        ):
            return None
        try:
            self._validate_controlled_directory(self._backup_directory)
        except BackupValidationError:
            return None
        target = self._data_directory / relative
        if target.parent != self._backup_directory or target.is_symlink():
            return None
        return target

    def cleanup_temporary_artifacts(self) -> int:
        if not self._temporary_directory.exists():
            return 0
        self._validate_controlled_directory(self._temporary_directory)
        removed = 0
        for path in self._temporary_directory.glob("job-[0-9][0-9][0-9][0-9][0-9][0-9]-*"):
            if path.is_symlink() or not path.is_dir():
                continue
            if not path.resolve().is_relative_to(self._temporary_directory.resolve()):
                continue
            shutil.rmtree(path)
            removed += 1
        return removed

    def cleanup_interrupted_artifacts(self, job_ids: tuple[int, ...]) -> int:
        if not self._backup_directory.exists():
            return 0
        self._validate_controlled_directory(self._backup_directory)
        removed = 0
        for job_id in job_ids:
            if job_id <= 0:
                continue
            pattern = f"palworld-manager-backup-*-j{job_id:06d}-*.tar.gz"
            for path in self._backup_directory.glob(pattern):
                relative = f"{BACKUP_DIRECTORY_NAME}/{path.name}"
                if self.remove_managed_artifact(relative):
                    removed += 1
        return removed

    def _validate_controlled_directory(self, directory: Path) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise BackupValidationError("diretório controlado de backup inválido")
        current = self._data_directory
        for part in directory.relative_to(self._data_directory).parts:
            current /= part
            if current.is_symlink():
                raise BackupValidationError("diretório controlado contém link simbólico")
        if not directory.resolve().is_relative_to(self._data_directory):
            raise BackupValidationError("diretório controlado escapou da área do Manager")

    @staticmethod
    def _write_archive(
        target: Path,
        payload_root: Path,
        files: tuple[ManifestFile, ...],
        manifest: bytes,
        created_at: datetime,
    ) -> None:
        timestamp = int(created_at.timestamp())
        with tarfile.open(target, mode="x:gz", format=tarfile.PAX_FORMAT) as archive:
            manifest_info = tarfile.TarInfo(MANIFEST_FILENAME)
            manifest_info.size = len(manifest)
            _normalize_tar_info(manifest_info, timestamp)
            archive.addfile(manifest_info, io.BytesIO(manifest))
            for entry in files:
                path = entry.path
                source = payload_root / Path(path)
                info = archive.gettarinfo(str(source), arcname=path)
                if not info.isfile():
                    raise BackupValidationError("o payload contém entrada não regular")
                _normalize_tar_info(info, timestamp)
                with source.open("rb") as stream:
                    archive.addfile(info, stream)


def _normalize_tar_info(info: tarfile.TarInfo, timestamp: int) -> None:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o600
    info.mtime = timestamp
