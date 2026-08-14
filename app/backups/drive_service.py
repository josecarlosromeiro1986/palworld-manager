import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

from app.backups.manifest import BackupValidationError, sha256_file, validate_archive
from app.backups.service import (
    BACKUP_DIRECTORY_NAME,
    BACKUP_FILENAME_PATTERN,
    BackupArtifact,
    LocalBackupService,
)
from app.db.models import BackupRecord
from app.integrations.google_drive import (
    DriveFile,
    DriveQuota,
    GoogleDriveError,
    GoogleDriveStorage,
)

DRIVE_TEMPORARY_DIRECTORY_NAME: Final = "tmp/drive"


class DriveTransferService:
    def __init__(
        self,
        *,
        manager_database: Path,
        local_backups: LocalBackupService,
        storage: GoogleDriveStorage,
    ) -> None:
        self._data_directory = manager_database.parent.resolve()
        self._backup_directory = self._data_directory / BACKUP_DIRECTORY_NAME
        self._temporary_directory = self._data_directory / DRIVE_TEMPORARY_DIRECTORY_NAME
        self._local_backups = local_backups
        self._storage = storage

    def status(self) -> tuple[DriveQuota, int]:
        quota = self._storage.quota()
        files = self._storage.list_files()
        return quota, len(files)

    def quota(self) -> DriveQuota:
        return self._storage.quota()

    @property
    def local_backups(self) -> LocalBackupService:
        return self._local_backups

    def upload(
        self,
        record: BackupRecord,
        *,
        job_id: int,
        cancel_requested: Callable[[], bool],
    ) -> DriveFile:
        source = self._local_backups.resolve_managed_artifact(record.storage_path)
        if (
            record.location != "LOCAL"
            or record.status != "VALID"
            or record.sha256 is None
            or record.size_bytes is None
            or source is None
            or source.is_symlink()
            or not source.is_file()
        ):
            raise GoogleDriveError("backup local não está disponível para upload")
        if source.stat().st_size != record.size_bytes or sha256_file(source) != record.sha256:
            raise GoogleDriveError("integridade do backup local divergiu")
        validate_archive(source)
        return self._storage.upload(
            source,
            record.filename,
            job_id=job_id,
            expected_sha256=record.sha256,
            cancel_requested=cancel_requested,
        )

    def download(
        self,
        record: BackupRecord,
        *,
        job_id: int,
        cancel_requested: Callable[[], bool],
    ) -> BackupArtifact:
        if (
            record.location != "DRIVE"
            or record.status != "VALID"
            or record.sha256 is None
            or record.size_bytes is None
            or BACKUP_FILENAME_PATTERN.fullmatch(record.filename) is None
            or record.storage_path != record.filename
        ):
            raise GoogleDriveError("registro remoto inválido")
        self._backup_directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        self._temporary_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._validate_controlled_directory(self._backup_directory)
        self._validate_controlled_directory(self._temporary_directory)
        working_directory = Path(
            tempfile.mkdtemp(prefix=f"job-{job_id:06d}-", dir=self._temporary_directory)
        )
        partial = working_directory / f"{record.filename}.partial"
        final = self._backup_directory / record.filename
        published = False
        try:
            if final.exists() or final.is_symlink():
                raise GoogleDriveError("o backup local já existe")
            self._storage.download(
                record.filename,
                partial,
                expected_sha256=record.sha256,
                cancel_requested=cancel_requested,
            )
            if (
                partial.is_symlink()
                or not partial.is_file()
                or partial.stat().st_size != record.size_bytes
                or sha256_file(partial) != record.sha256
            ):
                raise GoogleDriveError("download remoto inválido")
            manifest = validate_archive(partial)
            backup_id = manifest.get("backup_id")
            if not isinstance(backup_id, str):
                raise BackupValidationError("identificador do manifest inválido")
            if cancel_requested():
                from app.integrations.google_drive import GoogleDriveCancelled

                raise GoogleDriveCancelled("transferência cancelada")
            os.replace(partial, final)
            os.chmod(final, 0o640)
            published = True
            validate_archive(final)
            if sha256_file(final) != record.sha256:
                raise GoogleDriveError("SHA-256 mudou durante a publicação local")
            return BackupArtifact(
                backup_id=backup_id,
                filename=record.filename,
                storage_path=f"{BACKUP_DIRECTORY_NAME}/{record.filename}",
                sha256=record.sha256,
                size_bytes=record.size_bytes,
                created_at=record.created_at,
            )
        except Exception:
            if published:
                self._local_backups.remove_managed_artifact(
                    f"{BACKUP_DIRECTORY_NAME}/{record.filename}"
                )
            raise
        finally:
            shutil.rmtree(working_directory, ignore_errors=True)

    def delete(self, record: BackupRecord) -> None:
        if (
            record.location != "DRIVE"
            or record.status != "VALID"
            or record.storage_path != record.filename
            or BACKUP_FILENAME_PATTERN.fullmatch(record.filename) is None
        ):
            raise GoogleDriveError("registro remoto não gerenciado")
        self._storage.delete(record.filename)

    def remove_uploaded_artifact(self, filename: str) -> None:
        if BACKUP_FILENAME_PATTERN.fullmatch(filename) is None:
            raise GoogleDriveError("nome remoto não gerenciado")
        self._storage.delete(filename)

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

    def cleanup_interrupted_uploads(self, job_ids: tuple[int, ...]) -> int:
        return self._storage.cleanup_interrupted_uploads(job_ids)

    def _validate_controlled_directory(self, directory: Path) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise BackupValidationError("diretório controlado do Drive inválido")
        current = self._data_directory
        try:
            parts = directory.relative_to(self._data_directory).parts
        except ValueError as error:
            raise BackupValidationError("diretório do Drive escapou da área do Manager") from error
        for part in parts:
            current /= part
            if current.is_symlink():
                raise BackupValidationError("diretório do Drive contém link simbólico")
        if not directory.resolve().is_relative_to(self._data_directory):
            raise BackupValidationError("diretório do Drive escapou da área do Manager")
