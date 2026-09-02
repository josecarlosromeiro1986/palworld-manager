from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_AUTOMATIC,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_CANCELLED,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.backups.drive_service import DriveTransferService
from app.backups.jobs import apply_local_retention, register_backup_artifact
from app.backups.service import BACKUP_FILENAME_PATTERN
from app.db.engine import session_scope
from app.db.models import BackupRecord, Job
from app.integrations.google_drive import GoogleDriveCancelled, GoogleDriveError
from app.jobs.service import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STEP_CANCELLED,
    JOB_STEP_COMPLETED,
    JOB_STEP_FAILED,
    JOB_STEP_WAITING,
)
from app.manager_settings.service import (
    configured_drive_retention,
    configured_local_retention,
)
from app.notifications.service import DRIVE_FAILED, enqueue_discord_notification

DRIVE_CHECK_JOB_KIND: Final = "DRIVE_CHECK"
DRIVE_UPLOAD_JOB_KIND: Final = "DRIVE_UPLOAD"
DRIVE_DOWNLOAD_JOB_KIND: Final = "DRIVE_DOWNLOAD"
DRIVE_DELETE_JOB_KIND: Final = "DRIVE_DELETE"
DRIVE_JOB_KINDS: Final = (
    DRIVE_CHECK_JOB_KIND,
    DRIVE_UPLOAD_JOB_KIND,
    DRIVE_DOWNLOAD_JOB_KIND,
    DRIVE_DELETE_JOB_KIND,
)


class DriveJobRequestError(RuntimeError):
    """A solicitação de operação no Drive é inválida."""


class DriveJobConflictError(RuntimeError):
    """Já existe uma operação incompatível para o mesmo alvo."""


@dataclass(frozen=True, slots=True)
class DriveJobView:
    id: int
    status: str
    step: str
    progress: int
    is_cancellable: bool
    operation: str
    filename: str | None
    error: str | None
    quota_total: str | None
    quota_used: str | None
    quota_free: str | None
    remote_count: int | None


def enqueue_drive_check(session: Session, *, user_id: int) -> Job:
    return _enqueue(
        session,
        kind=DRIVE_CHECK_JOB_KIND,
        coordination_key="DRIVE_CHECK",
        user_id=user_id,
        result={"operation": "CHECK"},
        cancellable=False,
        audit_action="DRIVE_CHECK_REQUESTED",
    )


def enqueue_drive_upload(
    session: Session,
    *,
    backup_record_id: int,
    user_id: int | None,
    trigger: str,
) -> Job:
    if trigger not in {"AUTOMATIC", "MANUAL"}:
        raise ValueError("origem de upload inválida")
    record = session.get(BackupRecord, backup_record_id)
    if not _valid_record(record, "LOCAL"):
        raise DriveJobRequestError("Backup local válido não encontrado.")
    assert record is not None
    if (
        session.scalar(
            select(BackupRecord.id).where(
                BackupRecord.location == "DRIVE",
                BackupRecord.filename == record.filename,
                BackupRecord.status == "VALID",
            )
        )
        is not None
    ):
        raise DriveJobRequestError("Este backup já está no Google Drive.")
    return _enqueue(
        session,
        kind=DRIVE_UPLOAD_JOB_KIND,
        coordination_key=f"DRIVE_LOCAL_{record.id}",
        user_id=user_id,
        result={
            "operation": "UPLOAD",
            "backup_record_id": record.id,
            "filename": record.filename,
            "trigger": trigger,
        },
        cancellable=True,
        audit_action="DRIVE_UPLOAD_REQUESTED",
    )


def enqueue_drive_download(
    session: Session,
    *,
    backup_record_id: int,
    user_id: int,
) -> Job:
    record = session.get(BackupRecord, backup_record_id)
    if not _valid_record(record, "DRIVE"):
        raise DriveJobRequestError("Backup remoto válido não encontrado.")
    assert record is not None
    if (
        session.scalar(
            select(BackupRecord.id).where(
                BackupRecord.location == "LOCAL",
                BackupRecord.filename == record.filename,
                BackupRecord.status == "VALID",
            )
        )
        is not None
    ):
        raise DriveJobRequestError("Este backup já está disponível localmente.")
    return _enqueue(
        session,
        kind=DRIVE_DOWNLOAD_JOB_KIND,
        coordination_key=f"DRIVE_REMOTE_{record.id}",
        user_id=user_id,
        result={
            "operation": "DOWNLOAD",
            "backup_record_id": record.id,
            "filename": record.filename,
        },
        cancellable=True,
        audit_action="DRIVE_DOWNLOAD_REQUESTED",
    )


def enqueue_drive_delete(
    session: Session,
    *,
    backup_record_id: int,
    user_id: int,
) -> Job:
    record = session.get(BackupRecord, backup_record_id)
    if not _valid_record(record, "DRIVE"):
        raise DriveJobRequestError("Backup remoto válido não encontrado.")
    assert record is not None
    return _enqueue(
        session,
        kind=DRIVE_DELETE_JOB_KIND,
        coordination_key=f"DRIVE_REMOTE_{record.id}",
        user_id=user_id,
        result={
            "operation": "DELETE",
            "backup_record_id": record.id,
            "filename": record.filename,
        },
        cancellable=False,
        audit_action="DRIVE_DELETE_REQUESTED",
    )


def request_drive_cancel(session: Session, job_id: int, *, user_id: int) -> bool:
    changed = session.scalar(
        update(Job)
        .where(
            Job.id == job_id,
            Job.kind.in_(DRIVE_JOB_KINDS),
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.is_cancellable.is_(True),
            Job.cancel_requested.is_(False),
        )
        .values(cancel_requested=True)
        .returning(Job.id)
    )
    if changed is None:
        return False
    record_audit_event(
        session,
        occurred_at=datetime.now(UTC),
        action="DRIVE_CANCEL_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job_id,
        target="Google Drive",
    )
    return True


class DriveJobExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        service: DriveTransferService,
    ) -> None:
        self._session_factory = session_factory
        self._service = service

    def execute(self, job_id: int) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.kind not in DRIVE_JOB_KINDS or job.status != JOB_STATUS_RUNNING:
                raise ValueError("job do Drive inválido")
            kind = job.kind
            initial_result = dict(job.result or {})
        try:
            if kind == DRIVE_CHECK_JOB_KIND:
                self._execute_check(job_id)
            elif kind == DRIVE_UPLOAD_JOB_KIND:
                self._execute_upload(job_id, initial_result)
            elif kind == DRIVE_DOWNLOAD_JOB_KIND:
                self._execute_download(job_id, initial_result)
            elif kind == DRIVE_DELETE_JOB_KIND:
                self._execute_delete(job_id, initial_result)
            else:
                raise AssertionError("tipo de job remoto inesperado")
        except GoogleDriveCancelled:
            self._finish_cancelled(job_id, initial_result)
        except Exception:
            self._finish_failed(job_id, initial_result)
            raise

    def _execute_check(self, job_id: int) -> None:
        self._checkpoint(job_id, "CHECKING_CONNECTION", 30, False)
        quota, remote_count = self._service.status()
        self._finish_success(
            job_id,
            {
                "operation": "CHECK",
                "quota_total": quota.total_bytes,
                "quota_used": quota.used_bytes,
                "quota_free": quota.free_bytes,
                "quota_trashed": quota.trashed_bytes,
                "remote_count": remote_count,
            },
            audit_action="DRIVE_CHECK",
        )

    def _execute_upload(self, job_id: int, result: dict[str, object]) -> None:
        record_id = _result_record_id(result)
        self._checkpoint(job_id, "VALIDATING_LOCAL", 10, True)
        with session_scope(self._session_factory) as session:
            record = session.get(BackupRecord, record_id)
            if not _valid_record(record, "LOCAL"):
                raise GoogleDriveError("backup local não está mais disponível")
            assert record is not None and record.size_bytes is not None
            required_bytes = record.size_bytes
        self._checkpoint(job_id, "CHECKING_QUOTA", 20, True)
        self._prepare_capacity(job_id, required_bytes)
        self._checkpoint(job_id, "UPLOADING", 35, True)
        with session_scope(self._session_factory) as session:
            record = session.get_one(BackupRecord, record_id)
            remote = self._service.upload(
                record,
                job_id=job_id,
                cancel_requested=lambda: self._cancel_requested(job_id),
            )
            created_at = record.created_at
        self._checkpoint(job_id, "VERIFYING_REMOTE", 90, False)
        try:
            with session_scope(self._session_factory) as session:
                if (
                    session.scalar(
                        select(BackupRecord.id).where(
                            BackupRecord.location == "DRIVE",
                            BackupRecord.filename == remote.filename,
                        )
                    )
                    is not None
                ):
                    raise GoogleDriveError("registro remoto duplicado")
                remote_record = BackupRecord(
                    job_id=job_id,
                    filename=remote.filename,
                    location="DRIVE",
                    status="VALID",
                    sha256=remote.sha256,
                    size_bytes=remote.size_bytes,
                    storage_path=remote.filename,
                    created_at=created_at,
                )
                session.add(remote_record)
                session.flush()
                completed_result = {
                    **result,
                    "remote_backup_record_id": remote_record.id,
                    "sha256": remote.sha256,
                    "size_bytes": remote.size_bytes,
                    "integrity": "VALID",
                }
                job = session.get_one(Job, job_id)
                now = datetime.now(UTC)
                job.status = JOB_STATUS_SUCCEEDED
                job.step = JOB_STEP_COMPLETED
                job.progress = 100
                job.is_cancellable = False
                job.finished_at = now
                job.result = completed_result
                record_audit_event(
                    session,
                    occurred_at=now,
                    action="DRIVE_UPLOAD",
                    result=AUDIT_RESULT_SUCCESS,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job.id,
                    target="Google Drive",
                    details=_safe_audit_details(completed_result),
                )
        except Exception:
            self._service.remove_uploaded_artifact(remote.filename)
            raise

    def _execute_download(self, job_id: int, result: dict[str, object]) -> None:
        record_id = _result_record_id(result)
        self._checkpoint(job_id, "DOWNLOADING", 20, True)
        with session_scope(self._session_factory) as session:
            record = session.get_one(BackupRecord, record_id)
            artifact = self._service.download(
                record,
                job_id=job_id,
                cancel_requested=lambda: self._cancel_requested(job_id),
            )
        self._checkpoint(job_id, "VALIDATING_DOWNLOAD", 85, False)
        try:
            with session_scope(self._session_factory) as session:
                local_record = register_backup_artifact(session, artifact, job_id=job_id)
                apply_local_retention(
                    session,
                    self._service.local_backups,
                    configured_local_retention(session),
                    preserve_record_ids=(local_record.id,),
                )
                local_record_id = local_record.id
                completed_result = {
                    **result,
                    "local_backup_record_id": local_record_id,
                    "integrity": "VALID",
                }
                job = session.get_one(Job, job_id)
                now = datetime.now(UTC)
                job.status = JOB_STATUS_SUCCEEDED
                job.step = JOB_STEP_COMPLETED
                job.progress = 100
                job.is_cancellable = False
                job.finished_at = now
                job.result = completed_result
                record_audit_event(
                    session,
                    occurred_at=now,
                    action="DRIVE_DOWNLOAD",
                    result=AUDIT_RESULT_SUCCESS,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job.id,
                    target="Google Drive",
                    details=_safe_audit_details(completed_result),
                )
        except Exception:
            self._service.local_backups.remove_managed_artifact(artifact.storage_path)
            raise

    def _execute_delete(self, job_id: int, result: dict[str, object]) -> None:
        record_id = _result_record_id(result)
        self._checkpoint(job_id, "DELETING_REMOTE", 50, False)
        with session_scope(self._session_factory) as session:
            record = session.get_one(BackupRecord, record_id)
            self._service.delete(record)
            session.delete(record)
        self._finish_success(job_id, result, audit_action="DRIVE_DELETE")

    def _prepare_capacity(self, job_id: int, required_bytes: int) -> None:
        with session_scope(self._session_factory) as session:
            retention = configured_drive_retention(session)
        while self._managed_remote_count() >= retention:
            self._remove_oldest_managed_remote(job_id)
        quota = self._service.quota()
        available_bytes = quota.free_bytes
        while available_bytes < required_bytes and self._managed_remote_count() > 0:
            if self._cancel_requested(job_id):
                raise GoogleDriveCancelled("transferência cancelada")
            removed_bytes = self._remove_oldest_managed_remote(job_id)
            quota = self._service.quota()
            available_bytes = max(available_bytes + removed_bytes, quota.free_bytes)
        if available_bytes < required_bytes:
            raise GoogleDriveError("quota remota insuficiente")

    def _managed_remote_count(self) -> int:
        with session_scope(self._session_factory) as session:
            return len(_managed_remote_records(session))

    def _remove_oldest_managed_remote(self, job_id: int) -> int:
        with session_scope(self._session_factory) as session:
            records = _managed_remote_records(session)
            if not records:
                return 0
            oldest = records[-1]
            removed_bytes = oldest.size_bytes or 0
            self._service.delete(oldest)
            record_audit_event(
                session,
                occurred_at=datetime.now(UTC),
                action="DRIVE_RETENTION",
                result=AUDIT_RESULT_SUCCESS,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job_id,
                target="Google Drive",
                details={
                    "backup_record_id": oldest.id,
                    "filename": oldest.filename,
                    "reason": "RETENTION_OR_QUOTA",
                },
            )
            session.delete(oldest)
            return removed_bytes

    def _cancel_requested(self, job_id: int) -> bool:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            return bool(job.cancel_requested and job.is_cancellable)

    def _checkpoint(self, job_id: int, step: str, progress: int, cancellable: bool) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.status != JOB_STATUS_RUNNING:
                raise RuntimeError("estado inesperado do job do Drive")
            if job.cancel_requested and job.is_cancellable:
                raise GoogleDriveCancelled("transferência cancelada")
            job.step = step
            job.progress = progress
            job.is_cancellable = cancellable

    def _finish_success(
        self,
        job_id: int,
        result: dict[str, object],
        *,
        audit_action: str,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            now = datetime.now(UTC)
            job.status = JOB_STATUS_SUCCEEDED
            job.step = JOB_STEP_COMPLETED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = now
            job.result = result
            record_audit_event(
                session,
                occurred_at=now,
                action=audit_action,
                result=AUDIT_RESULT_SUCCESS,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Google Drive",
                details=_safe_audit_details(result),
            )

    def _finish_cancelled(self, job_id: int, result: dict[str, object]) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            now = datetime.now(UTC)
            job.status = JOB_STATUS_CANCELLED
            job.step = JOB_STEP_CANCELLED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = now
            job.result = {**result, "error": "CANCELLED"}
            record_audit_event(
                session,
                occurred_at=now,
                action=f"DRIVE_{result.get('operation', 'OPERATION')!s}",
                result=AUDIT_RESULT_CANCELLED,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Google Drive",
                details=_safe_audit_details(result),
            )

    def _finish_failed(self, job_id: int, result: dict[str, object]) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            now = datetime.now(UTC)
            job.status = JOB_STATUS_FAILED
            job.step = JOB_STEP_FAILED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = now
            job.result = {**result, "error": "DRIVE_FAILED"}
            record_audit_event(
                session,
                occurred_at=now,
                action=f"DRIVE_{result.get('operation', 'OPERATION')!s}",
                result=AUDIT_RESULT_FAILURE,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Google Drive",
                details={**_safe_audit_details(result), "error": "DRIVE_FAILED"},
            )
            enqueue_discord_notification(session, DRIVE_FAILED, job_id=job.id)


def drive_job_view(job: Job) -> DriveJobView:
    if job.kind not in DRIVE_JOB_KINDS:
        raise ValueError("job não pertence ao Google Drive")
    result = job.result or {}
    return DriveJobView(
        id=job.id,
        status=job.status,
        step=job.step,
        progress=job.progress,
        is_cancellable=job.is_cancellable,
        operation=str(result.get("operation", "CHECK")),
        filename=_optional_str(result.get("filename")),
        error=_optional_str(result.get("error")),
        quota_total=_optional_bytes(result.get("quota_total")),
        quota_used=_optional_bytes(result.get("quota_used")),
        quota_free=_optional_bytes(result.get("quota_free")),
        remote_count=_optional_int(result.get("remote_count")),
    )


def latest_drive_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job).where(Job.kind.in_(DRIVE_JOB_KINDS)).order_by(Job.id.desc()).limit(1)
    )


def _enqueue(
    session: Session,
    *,
    kind: str,
    coordination_key: str,
    user_id: int | None,
    result: dict[str, object],
    cancellable: bool,
    audit_action: str,
) -> Job:
    if (
        session.scalar(
            select(Job.id).where(
                Job.coordination_key == coordination_key,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        is not None
    ):
        raise DriveJobConflictError("Já existe uma operação do Drive para este backup.")
    job = Job(
        kind=kind,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=cancellable,
        requires_maintenance_lock=kind != DRIVE_CHECK_JOB_KIND,
        coordination_key=coordination_key,
        result=result,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise DriveJobConflictError("Já existe uma operação do Drive para este backup.") from error
    record_audit_event(
        session,
        occurred_at=datetime.now(UTC),
        action=audit_action,
        result=AUDIT_RESULT_SUCCESS,
        origin=(
            AUDIT_ORIGIN_ADMINISTRATOR
            if user_id is not None
            else AUDIT_ORIGIN_AUTOMATIC
            if result.get("trigger") == "AUTOMATIC"
            else AUDIT_ORIGIN_SYSTEM
        ),
        user_id=user_id,
        job_id=job.id,
        target="Google Drive",
        details=_safe_audit_details(result),
    )
    return job


def _valid_record(record: BackupRecord | None, location: str) -> bool:
    return bool(
        record is not None
        and record.location == location
        and record.status == "VALID"
        and record.sha256 is not None
        and record.size_bytes is not None
        and BACKUP_FILENAME_PATTERN.fullmatch(record.filename) is not None
        and (
            record.storage_path == record.filename
            if location == "DRIVE"
            else record.storage_path == f"backups/{record.filename}"
        )
    )


def _managed_remote_records(session: Session) -> tuple[BackupRecord, ...]:
    records = tuple(
        session.scalars(
            select(BackupRecord)
            .where(BackupRecord.location == "DRIVE", BackupRecord.status == "VALID")
            .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        )
    )
    return tuple(
        record
        for record in records
        if record.storage_path == record.filename
        and BACKUP_FILENAME_PATTERN.fullmatch(record.filename) is not None
    )


def _result_record_id(result: dict[str, object]) -> int:
    value = result.get("backup_record_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GoogleDriveError("referência de backup inválida")
    return value


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bytes(value: object) -> str | None:
    parsed = _optional_int(value)
    if parsed is None:
        return None
    amount = float(parsed)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _safe_audit_details(result: dict[str, object]) -> dict[str, object]:
    allowed = {
        "operation",
        "backup_record_id",
        "remote_backup_record_id",
        "local_backup_record_id",
        "filename",
        "trigger",
        "size_bytes",
        "integrity",
        "remote_count",
    }
    return {key: value for key, value in result.items() if key in allowed}
