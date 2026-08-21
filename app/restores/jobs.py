from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.backups.drive_service import DriveTransferService, TemporaryDriveDownload
from app.backups.jobs import (
    DEFAULT_LOCAL_RETENTION,
    apply_local_retention,
    register_backup_artifact,
)
from app.backups.manifest import BackupValidationError
from app.backups.service import BackupArtifact, LocalBackupService
from app.db.engine import session_scope
from app.db.models import BackupRecord, Job
from app.integrations.google_drive import GoogleDriveError
from app.jobs.service import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STEP_COMPLETED,
    JOB_STEP_FAILED,
    JOB_STEP_WAITING,
)
from app.lifecycle.jobs import lifecycle_timeout
from app.lifecycle.service import LifecycleAction, LifecycleExecutor, LifecycleOutcome
from app.logs.service import LogCategory, PalworldLogError, PalworldLogSource
from app.notifications.service import (
    RESTORE_COMPLETED,
    RESTORE_FAILED,
    enqueue_discord_notification,
)
from app.restores.service import LocalRestoreService, PreparedRestore, RestoreError

LOCAL_RESTORE_JOB_KIND: Final = "LOCAL_RESTORE"
REMOTE_RESTORE_JOB_KIND: Final = "REMOTE_RESTORE"
RESTORE_JOB_KINDS: Final = (LOCAL_RESTORE_JOB_KIND, REMOTE_RESTORE_JOB_KIND)
LOCAL_RESTORE_COORDINATION_KEY: Final = "LOCAL_RESTORE"
RESTORE_CONFIRMATION: Final = "RESTAURAR"


class RestoreJobConflictError(RuntimeError):
    """Já existe um Restore pendente ou em execução."""


class RestoreRequestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RestoreJobView:
    id: int
    status: str
    step: str
    progress: int
    source_filename: str
    source_location: str
    error: str | None
    requires_manual_review: bool
    preventive_backup_record_id: int | None


def enqueue_local_restore(
    session: Session,
    *,
    backup_record_id: int,
    confirmation: str,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    return _enqueue_restore(
        session,
        backup_record_id=backup_record_id,
        confirmation=confirmation,
        user_id=user_id,
        location="LOCAL",
        kind=LOCAL_RESTORE_JOB_KIND,
        occurred_at=occurred_at,
    )


def enqueue_remote_restore(
    session: Session,
    *,
    backup_record_id: int,
    confirmation: str,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    return _enqueue_restore(
        session,
        backup_record_id=backup_record_id,
        confirmation=confirmation,
        user_id=user_id,
        location="DRIVE",
        kind=REMOTE_RESTORE_JOB_KIND,
        occurred_at=occurred_at,
    )


def _enqueue_restore(
    session: Session,
    *,
    backup_record_id: int,
    confirmation: str,
    user_id: int,
    location: str,
    kind: str,
    occurred_at: datetime | None,
) -> Job:
    if confirmation != RESTORE_CONFIRMATION:
        raise RestoreRequestError("Digite RESTAURAR exatamente para confirmar.")
    record = session.get(BackupRecord, backup_record_id)
    if (
        record is None
        or record.location != location
        or record.status != "VALID"
        or record.sha256 is None
        or record.size_bytes is None
    ):
        raise RestoreRequestError("O backup selecionado não está disponível para Restore.")
    active = session.scalar(
        select(Job.id).where(
            Job.coordination_key == LOCAL_RESTORE_COORDINATION_KEY,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    if active is not None:
        raise RestoreJobConflictError("Já existe um Restore em andamento.")
    job = Job(
        kind=kind,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=False,
        requires_maintenance_lock=True,
        coordination_key=LOCAL_RESTORE_COORDINATION_KEY,
        result={
            "backup_record_id": record.id,
            "source_filename": record.filename,
            "source_location": location,
            "stop_timeout_seconds": lifecycle_timeout(session, LifecycleAction.STOP),
            "start_timeout_seconds": lifecycle_timeout(session, LifecycleAction.START),
            "destructive_started": False,
            "requires_manual_review": False,
        },
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as error:
        raise RestoreJobConflictError("Já existe um Restore em andamento.") from error
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action="RESTORE_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target=_source_target(location),
        details={
            "backup_record_id": record.id,
            "source_filename": record.filename,
            "source_location": location,
        },
    )
    return job


class LocalRestoreJobExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        restore_service: LocalRestoreService,
        backup_service: LocalBackupService,
        lifecycle: LifecycleExecutor,
        palworld_logs: PalworldLogSource,
        drive_service: DriveTransferService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._restore_service = restore_service
        self._backup_service = backup_service
        self._lifecycle = lifecycle
        self._palworld_logs = palworld_logs
        self._drive_service = drive_service

    def execute(self, job_id: int) -> None:
        prepared: PreparedRestore | None = None
        preventive: BackupArtifact | None = None
        preventive_record_id: int | None = None
        remote_download: TemporaryDriveDownload | None = None
        destructive_started = False
        request_data = self._load_request(job_id)
        try:
            with session_scope(self._session_factory) as session:
                record = session.get(
                    BackupRecord,
                    cast(int, request_data["backup_record_id"]),
                )
                if record is None:
                    raise RestoreError(
                        "BACKUP_UNAVAILABLE", "O backup selecionado não está mais disponível."
                    )
                session.expunge(record)
            if (
                record.filename != request_data["source_filename"]
                or record.location != request_data["source_location"]
            ):
                raise RestoreError(
                    "BACKUP_UNAVAILABLE", "O backup selecionado não está mais disponível."
                )
            if request_data["source_location"] == "DRIVE":
                if self._drive_service is None:
                    raise RestoreError(
                        "REMOTE_RESTORE_UNAVAILABLE",
                        "O Restore remoto não está disponível no worker.",
                    )
                self._checkpoint(
                    job_id,
                    "DOWNLOADING_REMOTE",
                    5,
                    destructive_started=False,
                )
                try:
                    remote_download = self._drive_service.download_temporary(
                        record,
                        job_id=job_id,
                        cancel_requested=lambda: False,
                    )
                except (GoogleDriveError, BackupValidationError) as error:
                    raise RestoreError(
                        "REMOTE_DOWNLOAD_INVALID",
                        "O backup remoto não pôde ser baixado e validado.",
                    ) from error
                self._checkpoint(job_id, "VALIDATING", 15, destructive_started=False)
                prepared = self._restore_service.prepare_remote(
                    record,
                    remote_download.archive_path,
                    job_id=job_id,
                )
                self._drive_service.cleanup_temporary_download(remote_download)
                remote_download = None
            else:
                self._checkpoint(job_id, "VALIDATING", 10, destructive_started=False)
                prepared = self._restore_service.prepare(record, job_id=job_id)

            self._checkpoint(job_id, "PREVENTIVE_BACKUP", 25, destructive_started=False)
            try:
                preventive = self._backup_service.create(
                    job_id=job_id,
                    trigger="MANUAL",
                    progress=lambda step, progress, _cancellable: self._checkpoint(
                        job_id,
                        f"PREVENTIVE_{step}",
                        min(45, 25 + progress // 5),
                        destructive_started=False,
                    ),
                )
            except Exception as error:
                raise RestoreError(
                    "PREVENTIVE_BACKUP_FAILED",
                    "O backup preventivo não pôde ser criado e validado.",
                ) from error
            with session_scope(self._session_factory) as session:
                preventive_record = register_backup_artifact(session, preventive, job_id=job_id)
                preventive_record_id = preventive_record.id
                apply_local_retention(
                    session,
                    self._backup_service,
                    DEFAULT_LOCAL_RETENTION,
                    preserve_record_ids=(
                        cast(int, request_data["backup_record_id"]),
                        preventive_record.id,
                    ),
                )
                record_audit_event(
                    session,
                    occurred_at=datetime.now(UTC),
                    action="BACKUP",
                    result=AUDIT_RESULT_SUCCESS,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job_id,
                    target="Backup preventivo do Restore",
                    details={
                        "backup_record_id": preventive_record.id,
                        "filename": preventive.filename,
                        "size_bytes": preventive.size_bytes,
                        "integrity": "VALID",
                        "role": "PRE_RESTORE",
                    },
                )

            self._checkpoint(job_id, "STOPPING", 50, destructive_started=False)
            stop_result = self._lifecycle.execute(
                LifecycleAction.STOP,
                cast(int, request_data["stop_timeout_seconds"]),
            )
            if stop_result.outcome is not LifecycleOutcome.SUCCEEDED:
                raise RestoreError("STOP_FAILED", "O Palworld não confirmou o estado offline.")

            destructive_started = True
            self._checkpoint(job_id, "RESTORING", 65, destructive_started=True)
            self._restore_service.apply(prepared, job_id=job_id)

            self._checkpoint(job_id, "STARTING", 85, destructive_started=True)
            start_requested_at = datetime.now(UTC)
            start_result = self._lifecycle.execute(
                LifecycleAction.START,
                cast(int, request_data["start_timeout_seconds"]),
            )
            if start_result.outcome is not LifecycleOutcome.SUCCEEDED:
                raise RestoreError("START_FAILED", "O Palworld não confirmou o estado online.")

            self._checkpoint(job_id, "VERIFYING", 95, destructive_started=True)
            self._verify_no_critical_logs(start_requested_at)
            self._complete(job_id, request_data, preventive_record_id)
        except RestoreError as error:
            self._fail(
                job_id,
                request_data,
                category=error.category,
                public_message=error.public_message,
                destructive_started=destructive_started,
                preventive_record_id=preventive_record_id,
            )
            return
        except Exception:
            if preventive is not None and preventive_record_id is None:
                self._backup_service.remove_managed_artifact(preventive.storage_path)
            self._fail(
                job_id,
                request_data,
                category="UNEXPECTED_FAILURE",
                public_message="O Restore falhou de forma inesperada.",
                destructive_started=destructive_started,
                preventive_record_id=preventive_record_id,
            )
            raise
        finally:
            if self._drive_service is not None:
                self._drive_service.cleanup_temporary_download(remote_download)
            self._restore_service.cleanup(prepared)

    def _load_request(self, job_id: int) -> dict[str, int | str]:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.kind not in RESTORE_JOB_KINDS or job.status != JOB_STATUS_RUNNING:
                raise ValueError("job de Restore não está em execução")
            result = job.result or {}
            record_id = result.get("backup_record_id")
            source_filename = result.get("source_filename")
            source_location = result.get("source_location", "LOCAL")
            stop_timeout = result.get("stop_timeout_seconds")
            start_timeout = result.get("start_timeout_seconds")
            expected_location = "DRIVE" if job.kind == REMOTE_RESTORE_JOB_KIND else "LOCAL"
            if (
                isinstance(record_id, bool)
                or not isinstance(record_id, int)
                or not isinstance(source_filename, str)
                or source_location not in {"LOCAL", "DRIVE"}
                or source_location != expected_location
                or isinstance(stop_timeout, bool)
                or not isinstance(stop_timeout, int)
                or isinstance(start_timeout, bool)
                or not isinstance(start_timeout, int)
            ):
                raise ValueError("job de Restore possui parâmetros inválidos")
            return {
                "backup_record_id": record_id,
                "source_filename": source_filename,
                "source_location": source_location,
                "stop_timeout_seconds": stop_timeout,
                "start_timeout_seconds": start_timeout,
            }

    def _checkpoint(
        self,
        job_id: int,
        step: str,
        progress: int,
        *,
        destructive_started: bool,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.status != JOB_STATUS_RUNNING:
                raise RuntimeError("estado inesperado do job de Restore")
            result = dict(job.result or {})
            result.update(
                {
                    "destructive_started": destructive_started,
                    "requires_manual_review": destructive_started,
                }
            )
            job.result = result
            job.step = step
            job.progress = progress
            job.is_cancellable = False

    def _verify_no_critical_logs(self, start_requested_at: datetime) -> None:
        try:
            entries = self._palworld_logs.history(100)
        except PalworldLogError as error:
            raise RestoreError(
                "POST_RESTORE_LOGS_UNAVAILABLE",
                "Os logs críticos não puderam ser verificados após o Restore.",
            ) from error
        for entry in entries:
            occurred_at = entry.occurred_at
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=UTC)
            if occurred_at >= start_requested_at and entry.category is LogCategory.ERROR:
                raise RestoreError(
                    "POST_RESTORE_CRITICAL_LOG",
                    "O Palworld registrou erro crítico após o Restore.",
                )

    def _complete(
        self,
        job_id: int,
        request_data: dict[str, int | str],
        preventive_record_id: int | None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            finished_at = datetime.now(UTC)
            job.status = JOB_STATUS_SUCCEEDED
            job.step = JOB_STEP_COMPLETED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = finished_at
            job.result = {
                **request_data,
                "preventive_backup_record_id": preventive_record_id,
                "destructive_started": True,
                "requires_manual_review": False,
                "final_state": "ONLINE",
            }
            record_audit_event(
                session,
                occurred_at=finished_at,
                action="RESTORE",
                result=AUDIT_RESULT_SUCCESS,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target=_source_target(cast(str, request_data["source_location"])),
                details={
                    "backup_record_id": request_data["backup_record_id"],
                    "source_filename": request_data["source_filename"],
                    "source_location": request_data["source_location"],
                    "preventive_backup_record_id": preventive_record_id,
                    "scope": "PALWORLD_ONLY",
                },
            )
            enqueue_discord_notification(session, RESTORE_COMPLETED, job_id=job.id)

    def _fail(
        self,
        job_id: int,
        request_data: dict[str, int | str],
        *,
        category: str,
        public_message: str,
        destructive_started: bool,
        preventive_record_id: int | None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            finished_at = datetime.now(UTC)
            job.status = JOB_STATUS_FAILED
            job.step = JOB_STEP_FAILED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = finished_at
            job.result = {
                **request_data,
                "preventive_backup_record_id": preventive_record_id,
                "destructive_started": destructive_started,
                "requires_manual_review": destructive_started,
                "error": category,
                "message": public_message,
            }
            record_audit_event(
                session,
                occurred_at=finished_at,
                action="RESTORE",
                result=AUDIT_RESULT_FAILURE,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target=_source_target(cast(str, request_data["source_location"])),
                details={
                    "backup_record_id": request_data["backup_record_id"],
                    "source_filename": request_data["source_filename"],
                    "source_location": request_data["source_location"],
                    "preventive_backup_record_id": preventive_record_id,
                    "error": category,
                    "requires_manual_review": destructive_started,
                },
            )
            enqueue_discord_notification(session, RESTORE_FAILED, job_id=job.id)


def restore_job_view(job: Job) -> RestoreJobView:
    if job.kind not in RESTORE_JOB_KINDS:
        raise ValueError("job não pertence ao Restore")
    result = job.result or {}
    source_filename = result.get("source_filename")
    source_location = result.get("source_location", "LOCAL")
    error = result.get("message")
    manual_review = result.get("requires_manual_review")
    preventive_id = result.get("preventive_backup_record_id")
    return RestoreJobView(
        id=job.id,
        status=job.status,
        step=job.step,
        progress=job.progress,
        source_filename=source_filename if isinstance(source_filename, str) else "indisponível",
        source_location=(source_location if source_location in {"LOCAL", "DRIVE"} else "LOCAL"),
        error=error if isinstance(error, str) else None,
        requires_manual_review=manual_review if isinstance(manual_review, bool) else False,
        preventive_backup_record_id=(
            preventive_id
            if isinstance(preventive_id, int) and not isinstance(preventive_id, bool)
            else None
        ),
    )


def latest_restore_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job).where(Job.kind.in_(RESTORE_JOB_KINDS)).order_by(Job.id.desc()).limit(1)
    )


def _source_target(location: str) -> str:
    return "Backup remoto" if location == "DRIVE" else "Backup local"
