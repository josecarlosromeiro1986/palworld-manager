from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import delete, select, update
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
from app.backups.service import BackupArtifact, BackupCancelledError, LocalBackupService
from app.db.engine import session_scope
from app.db.models import BackupRecord, Job
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
from app.manager_settings.service import configured_local_retention
from app.notifications.service import BACKUP_FAILED, enqueue_discord_notification

LOCAL_BACKUP_JOB_KIND: Final = "LOCAL_BACKUP"
LOCAL_BACKUP_COORDINATION_KEY: Final = "LOCAL_BACKUP"


class BackupJobConflictError(RuntimeError):
    """Já existe um backup local pendente ou em execução."""


@dataclass(frozen=True, slots=True)
class BackupJobView:
    id: int
    status: str
    step: str
    progress: int
    is_cancellable: bool
    trigger: str
    error: str | None


def enqueue_local_backup(
    session: Session,
    *,
    user_id: int | None,
    trigger: str,
    occurred_at: datetime | None = None,
) -> Job:
    if trigger not in {"MANUAL", "AUTOMATIC"}:
        raise ValueError("origem de backup inválida")
    if (
        session.scalar(
            select(Job.id).where(
                Job.coordination_key == LOCAL_BACKUP_COORDINATION_KEY,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        is not None
    ):
        raise BackupJobConflictError("Já existe um backup local em andamento.")
    job = Job(
        kind=LOCAL_BACKUP_JOB_KIND,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=True,
        requires_maintenance_lock=True,
        coordination_key=LOCAL_BACKUP_COORDINATION_KEY,
        result={"trigger": trigger},
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise BackupJobConflictError("Já existe um backup local em andamento.") from error
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action="BACKUP_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=(
            AUDIT_ORIGIN_ADMINISTRATOR
            if user_id is not None
            else AUDIT_ORIGIN_AUTOMATIC
            if trigger == "AUTOMATIC"
            else AUDIT_ORIGIN_SYSTEM
        ),
        user_id=user_id,
        job_id=job.id,
        target="Backup local",
        details={"trigger": trigger},
    )
    return job


def request_backup_cancel(session: Session, job_id: int, *, user_id: int) -> bool:
    changed = session.scalar(
        update(Job)
        .where(
            Job.id == job_id,
            Job.kind == LOCAL_BACKUP_JOB_KIND,
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
        action="BACKUP_CANCEL_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job_id,
        target="Backup local",
    )
    return True


class LocalBackupJobExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        service: LocalBackupService,
        *,
        automatic_drive_uploads: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._service = service
        self._automatic_drive_uploads = automatic_drive_uploads

    def execute(self, job_id: int) -> BackupArtifact | None:
        artifact: BackupArtifact | None = None
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            trigger = _job_trigger(job)
        try:
            artifact = self._service.create(
                job_id=job_id,
                trigger=trigger,
                progress=lambda step, progress, cancellable: self._checkpoint(
                    job_id, step, progress, cancellable
                ),
            )
            with session_scope(self._session_factory) as session:
                job = session.get_one(Job, job_id)
                record = register_backup_artifact(session, artifact, job_id=job.id)
                _apply_retention(
                    session,
                    self._service,
                    configured_local_retention(session),
                )
                drive_upload_job_id: int | None = None
                if trigger == "AUTOMATIC" and self._automatic_drive_uploads:
                    from app.backups.drive_jobs import enqueue_drive_upload

                    drive_job = enqueue_drive_upload(
                        session,
                        backup_record_id=record.id,
                        user_id=None,
                        trigger="AUTOMATIC",
                    )
                    drive_upload_job_id = drive_job.id
                job.status = JOB_STATUS_SUCCEEDED
                job.step = JOB_STEP_COMPLETED
                job.progress = 100
                job.is_cancellable = False
                completed_at = datetime.now(UTC)
                job.finished_at = completed_at
                job.result = {
                    "trigger": trigger,
                    "backup_record_id": record.id,
                    "filename": artifact.filename,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "integrity": "VALID",
                    "drive_upload_job_id": drive_upload_job_id,
                }
                record_audit_event(
                    session,
                    occurred_at=completed_at,
                    action="BACKUP",
                    result=AUDIT_RESULT_SUCCESS,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job.id,
                    target="Backup local",
                    details={
                        "backup_record_id": record.id,
                        "filename": artifact.filename,
                        "size_bytes": artifact.size_bytes,
                        "integrity": "VALID",
                        "trigger": trigger,
                    },
                )
            return artifact
        except BackupCancelledError:
            with session_scope(self._session_factory) as session:
                job = session.get_one(Job, job_id)
                completed_at = _finish_failed_job(
                    job, JOB_STATUS_CANCELLED, JOB_STEP_CANCELLED, "CANCELLED"
                )
                record_audit_event(
                    session,
                    occurred_at=completed_at,
                    action="BACKUP",
                    result=AUDIT_RESULT_CANCELLED,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job.id,
                    target="Backup local",
                    details={"trigger": trigger},
                )
            return None
        except Exception:
            if artifact is not None:
                self._service.remove_managed_artifact(artifact.storage_path)
            with session_scope(self._session_factory) as session:
                job = session.get_one(Job, job_id)
                completed_at = _finish_failed_job(
                    job, JOB_STATUS_FAILED, JOB_STEP_FAILED, "BACKUP_FAILED"
                )
                record_audit_event(
                    session,
                    occurred_at=completed_at,
                    action="BACKUP",
                    result=AUDIT_RESULT_FAILURE,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job.id,
                    target="Backup local",
                    details={"error": "BACKUP_FAILED", "trigger": trigger},
                )
                if trigger == "AUTOMATIC":
                    enqueue_discord_notification(session, BACKUP_FAILED, job_id=job.id)
            raise

    def _checkpoint(self, job_id: int, step: str, progress: int, cancellable: bool) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.status != JOB_STATUS_RUNNING:
                raise RuntimeError("estado inesperado do job de backup")
            if job.cancel_requested and job.is_cancellable:
                raise BackupCancelledError("backup cancelado")
            job.step = step
            job.progress = progress
            job.is_cancellable = cancellable


def backup_job_view(job: Job) -> BackupJobView:
    if job.kind != LOCAL_BACKUP_JOB_KIND:
        raise ValueError("job não pertence ao backup local")
    result = job.result or {}
    trigger = result.get("trigger")
    error = result.get("error")
    return BackupJobView(
        id=job.id,
        status=job.status,
        step=job.step,
        progress=job.progress,
        is_cancellable=job.is_cancellable,
        trigger=trigger if isinstance(trigger, str) else "MANUAL",
        error=error if isinstance(error, str) else None,
    )


def latest_backup_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job).where(Job.kind == LOCAL_BACKUP_JOB_KIND).order_by(Job.id.desc()).limit(1)
    )


def _job_trigger(job: Job) -> str:
    if job.kind != LOCAL_BACKUP_JOB_KIND or job.status != JOB_STATUS_RUNNING:
        raise ValueError("job de backup não está em execução")
    trigger = (job.result or {}).get("trigger")
    if trigger not in {"MANUAL", "AUTOMATIC"}:
        raise ValueError("job possui origem inválida")
    return str(trigger)


def _finish_failed_job(job: Job, status: str, step: str, error: str) -> datetime:
    trigger = (job.result or {}).get("trigger", "MANUAL")
    job.status = status
    job.step = step
    job.progress = 100
    job.is_cancellable = False
    completed_at = datetime.now(UTC)
    job.finished_at = completed_at
    job.result = {"trigger": trigger, "error": error}
    return completed_at


def register_backup_artifact(
    session: Session,
    artifact: BackupArtifact,
    *,
    job_id: int,
) -> BackupRecord:
    record = BackupRecord(
        job_id=job_id,
        filename=artifact.filename,
        location="LOCAL",
        status="VALID",
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        storage_path=artifact.storage_path,
        created_at=artifact.created_at,
    )
    session.add(record)
    session.flush()
    return record


def apply_local_retention(
    session: Session,
    service: LocalBackupService,
    retention: int,
    *,
    preserve_record_ids: tuple[int, ...] = (),
) -> None:
    records = tuple(
        session.scalars(
            select(BackupRecord)
            .where(BackupRecord.location == "LOCAL", BackupRecord.status == "VALID")
            .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        )
    )
    managed_records = tuple(
        record
        for record in records
        if service.resolve_managed_artifact(record.storage_path) is not None
    )
    protected = set(preserve_record_ids)
    kept: list[BackupRecord] = []
    for record in managed_records:
        if record.id in protected:
            kept.append(record)
    for record in managed_records:
        if len(kept) >= retention:
            break
        if record not in kept:
            kept.append(record)
    kept_ids = {record.id for record in kept}
    for record in managed_records:
        if record.id in kept_ids:
            continue
        service.remove_managed_artifact(record.storage_path)
        session.execute(delete(BackupRecord).where(BackupRecord.id == record.id))


def _apply_retention(session: Session, service: LocalBackupService, retention: int) -> None:
    """Compatibilidade interna para os testes e o executor de backup existentes."""
    apply_local_retention(session, service, retention)
