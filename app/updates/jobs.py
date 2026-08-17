from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_CANCELLED,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.backups.jobs import (
    DEFAULT_LOCAL_RETENTION,
    apply_local_retention,
    register_backup_artifact,
)
from app.backups.service import BackupArtifact, LocalBackupService
from app.db.engine import session_scope
from app.db.models import AppSetting, Job, NotificationEvent
from app.jobs.logs import JobLogStore, MemoryJobLogStore
from app.jobs.service import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STEP_CANCELLED,
    JOB_STEP_COMPLETED,
    JOB_STEP_COUNTDOWN,
    JOB_STEP_FAILED,
    JOB_STEP_INTERRUPTED,
    JOB_STEP_WAITING,
)
from app.lifecycle.jobs import LIFECYCLE_COORDINATION_KEY, lifecycle_timeout
from app.lifecycle.service import LifecycleAction, LifecycleExecutor, LifecycleOutcome
from app.logs.service import LogCategory, PalworldLogError, PalworldLogSource
from app.shutdown.jobs import assisted_shutdown_default
from app.shutdown.service import (
    AssistedShutdownExecutor,
    CountdownControl,
    CountdownDirective,
    ShutdownOutcome,
)
from app.updates.service import DiskSpaceSource, SteamBuildInfo, SteamCmdError, SteamCmdGateway

UPDATE_CHECK_JOB_KIND: Final = "PALWORLD_UPDATE_CHECK"
UPDATE_JOB_KIND: Final = "PALWORLD_UPDATE"
UPDATE_JOB_KINDS: Final = (UPDATE_CHECK_JOB_KIND, UPDATE_JOB_KIND)
UPDATE_CHECK_COORDINATION_KEY: Final = "PALWORLD_UPDATE_CHECK"
DISK_CRITICAL_KEY: Final = "disk_critical_gb"
DEFAULT_DISK_CRITICAL_GB: Final = 10


class UpdateJobConflictError(RuntimeError):
    """Já existe uma verificação ou ação incompatível em andamento."""


class UpdateRequestError(RuntimeError):
    """A solicitação de Update não possui uma verificação válida."""


class UpdateCancelledError(RuntimeError):
    """O Update foi cancelado antes da etapa crítica."""


class UpdateExecutionError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.public_message = message


@dataclass(frozen=True, slots=True)
class UpdateJobView:
    id: int
    kind: str
    status: str
    step: str
    progress: int
    is_cancellable: bool
    installed_build_id: str | None
    available_build_id: str | None
    available_at: datetime | None
    update_available: bool | None
    disk_critical: bool | None
    preventive_backup_record_id: int | None
    requires_manual_review: bool
    message: str | None


def enqueue_update_check(
    session: Session,
    *,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    _ensure_no_active_job(session, UPDATE_CHECK_COORDINATION_KEY)
    _ensure_no_active_job(session, LIFECYCLE_COORDINATION_KEY)
    job = Job(
        kind=UPDATE_CHECK_JOB_KIND,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=False,
        requires_maintenance_lock=False,
        coordination_key=UPDATE_CHECK_COORDINATION_KEY,
        result={},
    )
    session.add(job)
    _flush_job(session)
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action="PALWORLD_UPDATE_CHECK_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target="Palworld",
    )
    return job


def enqueue_update(
    session: Session,
    *,
    confirmation: str,
    user_id: int,
    occurred_at: datetime | None = None,
) -> Job:
    if confirmation != "ATUALIZAR":
        raise UpdateRequestError("Digite ATUALIZAR para confirmar.")
    _ensure_no_active_job(session, UPDATE_CHECK_COORDINATION_KEY)
    _ensure_no_active_job(session, LIFECYCLE_COORDINATION_KEY)
    checked = latest_update_check(session)
    if checked is None or checked.status != JOB_STATUS_SUCCEEDED:
        raise UpdateRequestError("Verifique as atualizações antes de iniciar o Update.")
    check_result = checked.result or {}
    if check_result.get("update_available") is not True:
        raise UpdateRequestError("Não há uma atualização confirmada para instalar.")
    if check_result.get("disk_critical") is True:
        raise UpdateRequestError("O espaço livre está em nível crítico; o Update foi bloqueado.")
    installed = _required_build_id(check_result, "installed_build_id")
    available = _required_build_id(check_result, "available_build_id")
    job = Job(
        kind=UPDATE_JOB_KIND,
        status=JOB_STATUS_PENDING,
        step=JOB_STEP_WAITING,
        progress=0,
        is_cancellable=True,
        requires_maintenance_lock=True,
        coordination_key=LIFECYCLE_COORDINATION_KEY,
        result={
            "checked_job_id": checked.id,
            "requested_installed_build_id": installed,
            "requested_available_build_id": available,
            "countdown_minutes": assisted_shutdown_default(session),
            "stop_timeout_seconds": lifecycle_timeout(session, LifecycleAction.STOP),
            "start_timeout_seconds": lifecycle_timeout(session, LifecycleAction.START),
            "disk_critical_gb": _disk_critical_gb(session),
        },
    )
    session.add(job)
    _flush_job(session)
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action="PALWORLD_UPDATE_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job.id,
        target="Palworld",
        details={
            "installed_build_id": installed,
            "available_build_id": available,
        },
    )
    return job


def request_update_cancel(session: Session, job_id: int, *, user_id: int) -> bool:
    old_status = session.scalar(
        update(Job)
        .where(
            Job.id == job_id,
            Job.kind == UPDATE_JOB_KIND,
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.is_cancellable.is_(True),
            Job.cancel_requested.is_(False),
        )
        .values(cancel_requested=True)
        .returning(Job.status)
    )
    if old_status is None:
        return False
    job = session.get_one(Job, job_id)
    requested_at = datetime.now(UTC)
    record_audit_event(
        session,
        occurred_at=requested_at,
        action="PALWORLD_UPDATE_CANCEL_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        job_id=job_id,
        target="Palworld",
    )
    if old_status == JOB_STATUS_PENDING:
        _mark_cancelled(session, job, result=dict(job.result or {}), occurred_at=requested_at)
    return True


class DatabaseUpdateCountdownControl(CountdownControl):
    def __init__(self, session_factory: sessionmaker[Session], job_id: int) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self.irreversible_started = False

    def update(self, remaining_seconds: int, total_seconds: int) -> CountdownDirective:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, self._job_id)
            job.step = JOB_STEP_COUNTDOWN
            job.progress = 50 + int(15 * (total_seconds - remaining_seconds) / total_seconds)
            result = dict(job.result or {})
            result["remaining_seconds"] = remaining_seconds
            job.result = result
            if job.cancel_requested:
                return CountdownDirective.CANCEL
            return CountdownDirective.CONTINUE

    def mark_irreversible(self) -> CountdownDirective:
        with session_scope(self._session_factory) as session:
            changed = session.scalar(
                update(Job)
                .where(
                    Job.id == self._job_id,
                    Job.is_cancellable.is_(True),
                    Job.cancel_requested.is_(False),
                )
                .values(is_cancellable=False, step="STOPPING", progress=68)
                .returning(Job.id)
            )
            job = session.get_one(Job, self._job_id)
            if changed is None and job.cancel_requested:
                return CountdownDirective.CANCEL
            self.irreversible_started = True
            job.is_cancellable = False
            job.step = "STOPPING"
            job.progress = 68
            result = dict(job.result or {})
            result["remaining_seconds"] = 0
            job.result = result
            return CountdownDirective.CONTINUE


class UpdateJobExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        steamcmd: SteamCmdGateway,
        disk_space: DiskSpaceSource,
        backup_service: LocalBackupService,
        assisted_shutdown: AssistedShutdownExecutor,
        lifecycle: LifecycleExecutor,
        palworld_logs: PalworldLogSource,
        *,
        job_logs: JobLogStore | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._steamcmd = steamcmd
        self._disk_space = disk_space
        self._backup_service = backup_service
        self._assisted_shutdown = assisted_shutdown
        self._lifecycle = lifecycle
        self._palworld_logs = palworld_logs
        self._job_logs = job_logs or MemoryJobLogStore()

    def execute(self, job_id: int) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            kind = job.kind
        if kind == UPDATE_CHECK_JOB_KIND:
            self._execute_check(job_id)
            return
        if kind != UPDATE_JOB_KIND:
            raise ValueError("job não pertence ao domínio de Update")
        self._execute_update(job_id)

    def _execute_check(self, job_id: int) -> None:
        try:
            self._checkpoint(job_id, "CHECKING_DISK", 15, cancellable=False)
            free_bytes = self._disk_space.free_bytes()
            with session_scope(self._session_factory) as session:
                critical_bytes = _disk_critical_gb(session) * 1024**3
            self._checkpoint(job_id, "CHECKING", 35, cancellable=False)
            build = self._steamcmd.check()
            result = {
                **_build_result(build),
                "disk_free_bytes": free_bytes,
                "disk_critical": free_bytes < critical_bytes,
            }
            with session_scope(self._session_factory) as session:
                job = session.get_one(Job, job_id)
                finished_at = datetime.now(UTC)
                job.status = JOB_STATUS_SUCCEEDED
                job.step = JOB_STEP_COMPLETED
                job.progress = 100
                job.is_cancellable = False
                job.finished_at = finished_at
                job.result = result
                record_audit_event(
                    session,
                    occurred_at=finished_at,
                    action="PALWORLD_UPDATE_CHECK",
                    result=AUDIT_RESULT_SUCCESS,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job.id,
                    target="Palworld",
                    details=result,
                )
            self._append_log(job_id, "Versões instalada e pública verificadas.")
        except Exception:
            self._fail(
                job_id,
                category="UPDATE_CHECK_FAILED",
                message="Não foi possível verificar as versões pelo SteamCMD.",
                requires_manual_review=False,
                notify=False,
            )

    def _execute_update(self, job_id: int) -> None:
        preventive: BackupArtifact | None = None
        preventive_record_id: int | None = None
        critical_started = False
        try:
            request_data = self._load_update_request(job_id)
        except Exception:
            self._fail(
                job_id,
                category="INVALID_UPDATE_JOB",
                message="O job de Update possui dados inválidos e não foi executado.",
                requires_manual_review=False,
            )
            return
        try:
            self._checkpoint(job_id, "CHECKING_DISK", 3, cancellable=True)
            free_bytes = self._disk_space.free_bytes()
            critical_bytes = cast(int, request_data["disk_critical_gb"]) * 1024**3
            if free_bytes < critical_bytes:
                self._notification(job_id, "DISK_CRITICAL")
                raise UpdateExecutionError(
                    "DISK_CRITICAL",
                    "O Update foi bloqueado porque o espaço livre está em nível crítico.",
                )

            self._checkpoint(job_id, "VERIFYING", 8, cancellable=True)
            before = self._steamcmd.check()
            self._merge_result(job_id, _build_result(before))
            if not before.update_available:
                self._complete_no_update(job_id, request_data, before)
                return

            self._checkpoint(job_id, "PREVENTIVE_BACKUP", 12, cancellable=True)
            try:
                preventive = self._backup_service.create(
                    job_id=job_id,
                    trigger="MANUAL",
                    progress=lambda step, progress, cancellable: self._checkpoint(
                        job_id,
                        f"PREVENTIVE_{step}",
                        min(47, 12 + progress // 3),
                        cancellable=cancellable,
                    ),
                )
            except UpdateCancelledError:
                raise
            except Exception as error:
                raise UpdateExecutionError(
                    "PREVENTIVE_BACKUP_FAILED",
                    "O backup pré-update não pôde ser criado e validado.",
                ) from error
            with session_scope(self._session_factory) as session:
                record = register_backup_artifact(session, preventive, job_id=job_id)
                preventive_record_id = record.id
                apply_local_retention(
                    session,
                    self._backup_service,
                    DEFAULT_LOCAL_RETENTION,
                    preserve_record_ids=(record.id,),
                )
                record_audit_event(
                    session,
                    occurred_at=datetime.now(UTC),
                    action="BACKUP",
                    result=AUDIT_RESULT_SUCCESS,
                    origin=AUDIT_ORIGIN_SYSTEM,
                    job_id=job_id,
                    target="Backup pré-update",
                    details={
                        "backup_record_id": record.id,
                        "filename": preventive.filename,
                        "size_bytes": preventive.size_bytes,
                        "integrity": "VALID",
                        "role": "PRE_UPDATE",
                    },
                )
            self._merge_result(job_id, {"preventive_backup_record_id": preventive_record_id})

            self._checkpoint(job_id, "ASSISTED_SHUTDOWN", 50, cancellable=True)
            countdown_control = DatabaseUpdateCountdownControl(self._session_factory, job_id)
            try:
                shutdown = self._assisted_shutdown.execute(
                    cast(int, request_data["countdown_minutes"]),
                    cast(int, request_data["stop_timeout_seconds"]),
                    countdown_control,
                )
            except Exception:
                critical_started = countdown_control.irreversible_started
                raise
            critical_started = countdown_control.irreversible_started
            if shutdown.outcome is ShutdownOutcome.CANCELLED:
                raise UpdateCancelledError("Update cancelado")
            if shutdown.outcome is not ShutdownOutcome.SUCCEEDED:
                raise UpdateExecutionError(
                    "STOP_FAILED",
                    "O Palworld não confirmou o estado offline; revise o estado manualmente.",
                )
            self._checkpoint(job_id, "UPDATING", 72, cancellable=False)
            try:
                self._steamcmd.apply_update()
            except SteamCmdError as error:
                raise UpdateExecutionError(
                    "STEAMCMD_FAILED",
                    "O SteamCMD não confirmou a atualização; "
                    "o servidor permanece parado para revisão.",
                ) from error

            self._checkpoint(job_id, "STARTING", 84, cancellable=False)
            start_requested_at = datetime.now(UTC)
            start = self._lifecycle.execute(
                LifecycleAction.START,
                cast(int, request_data["start_timeout_seconds"]),
            )
            if start.outcome is not LifecycleOutcome.SUCCEEDED:
                raise UpdateExecutionError(
                    "START_FAILED",
                    "O Palworld não voltou ao estado ONLINE após o Update.",
                )

            self._checkpoint(job_id, "VERIFYING_VERSION", 93, cancellable=False)
            after = self._steamcmd.check()
            if after.installed_build_id != after.available_build_id:
                raise UpdateExecutionError(
                    "VERSION_MISMATCH",
                    "A versão instalada não corresponde à branch pública após o Update.",
                )
            self._verify_no_critical_logs(start_requested_at)
            self._complete(job_id, request_data, before, after, preventive_record_id)
        except UpdateCancelledError:
            self._cancel(job_id, request_data, preventive_record_id)
        except UpdateExecutionError as error:
            self._fail(
                job_id,
                category=error.category,
                message=error.public_message,
                requires_manual_review=critical_started,
                preventive_record_id=preventive_record_id,
            )
        except Exception:
            self._fail(
                job_id,
                category="UPDATE_FAILED",
                message="O Update falhou de forma controlada.",
                requires_manual_review=critical_started,
                preventive_record_id=preventive_record_id,
            )
        finally:
            if preventive is not None and preventive_record_id is None:
                self._backup_service.remove_managed_artifact(preventive.storage_path)

    def _load_update_request(self, job_id: int) -> dict[str, object]:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.kind != UPDATE_JOB_KIND or job.status != JOB_STATUS_RUNNING:
                raise ValueError("job de Update não está em execução")
            data = dict(job.result or {})
        _required_build_id(data, "requested_installed_build_id")
        _required_build_id(data, "requested_available_build_id")
        _required_int(data, "countdown_minutes", minimum=0, maximum=10)
        _required_int(data, "stop_timeout_seconds", minimum=1, maximum=300)
        _required_int(data, "start_timeout_seconds", minimum=1, maximum=300)
        _required_int(data, "disk_critical_gb", minimum=1, maximum=1024)
        return data

    def _checkpoint(self, job_id: int, step: str, progress: int, *, cancellable: bool) -> None:
        changed_step = False
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.status != JOB_STATUS_RUNNING:
                raise RuntimeError("estado inesperado do job de Update")
            if job.cancel_requested and job.is_cancellable:
                raise UpdateCancelledError("Update cancelado")
            changed_step = job.step != step
            job.step = step
            job.progress = progress
            job.is_cancellable = cancellable
        if changed_step:
            self._append_log(job_id, _step_log_message(step))

    def _verify_no_critical_logs(self, start_requested_at: datetime) -> None:
        try:
            entries = self._palworld_logs.history(100)
        except PalworldLogError as error:
            raise UpdateExecutionError(
                "POST_UPDATE_LOGS_UNAVAILABLE",
                "Os logs críticos não puderam ser verificados após o Update.",
            ) from error
        for entry in entries:
            occurred_at = entry.occurred_at
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=UTC)
            if occurred_at >= start_requested_at and entry.category is LogCategory.ERROR:
                raise UpdateExecutionError(
                    "POST_UPDATE_CRITICAL_LOG",
                    "O Palworld registrou erro crítico após o Update.",
                )

    def _complete(
        self,
        job_id: int,
        request_data: dict[str, object],
        before: SteamBuildInfo,
        after: SteamBuildInfo,
        preventive_record_id: int | None,
    ) -> None:
        finished_at = datetime.now(UTC)
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            job.status = JOB_STATUS_SUCCEEDED
            job.step = JOB_STEP_COMPLETED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = finished_at
            job.result = {
                **request_data,
                "installed_build_id": after.installed_build_id,
                "available_build_id": after.available_build_id,
                "available_at": _datetime_value(after.available_at),
                "previous_build_id": before.installed_build_id,
                "update_available": False,
                "preventive_backup_record_id": preventive_record_id,
                "requires_manual_review": False,
                "message": "Update concluído e Palworld ONLINE.",
            }
            record_audit_event(
                session,
                occurred_at=finished_at,
                action="PALWORLD_UPDATE",
                result=AUDIT_RESULT_SUCCESS,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Palworld",
                details={
                    "previous_build_id": before.installed_build_id,
                    "installed_build_id": after.installed_build_id,
                    "preventive_backup_record_id": preventive_record_id,
                    "final_state": "ONLINE",
                },
            )
            session.add(
                NotificationEvent(
                    event_type="UPDATE_COMPLETED",
                    channel="DISCORD",
                    status="PENDING",
                    job_id=job_id,
                )
            )
        self._append_log(job_id, "Update validado; Palworld está ONLINE.")

    def _complete_no_update(
        self,
        job_id: int,
        request_data: dict[str, object],
        build: SteamBuildInfo,
    ) -> None:
        finished_at = datetime.now(UTC)
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            job.status = JOB_STATUS_SUCCEEDED
            job.step = JOB_STEP_COMPLETED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = finished_at
            job.result = {
                **request_data,
                **_build_result(build),
                "requires_manual_review": False,
                "message": "A versão instalada já corresponde à branch pública.",
            }
            record_audit_event(
                session,
                occurred_at=finished_at,
                action="PALWORLD_UPDATE",
                result=AUDIT_RESULT_SUCCESS,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Palworld",
                details={"outcome": "ALREADY_CURRENT", **_build_result(build)},
            )

    def _cancel(
        self,
        job_id: int,
        request_data: dict[str, object],
        preventive_record_id: int | None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            result = {
                **request_data,
                "preventive_backup_record_id": preventive_record_id,
                "requires_manual_review": False,
                "message": "Update cancelado antes do Stop.",
            }
            _mark_cancelled(session, job, result=result, occurred_at=datetime.now(UTC))
        self._append_log(job_id, "Update cancelado em ponto seguro.")

    def _fail(
        self,
        job_id: int,
        *,
        category: str,
        message: str,
        requires_manual_review: bool,
        preventive_record_id: int | None = None,
        notify: bool = True,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            previous = dict(job.result or {})
            finished_at = datetime.now(UTC)
            job.status = JOB_STATUS_FAILED
            job.step = JOB_STEP_FAILED
            job.progress = 100
            job.is_cancellable = False
            job.finished_at = finished_at
            job.result = {
                **previous,
                "error": category,
                "message": message,
                "preventive_backup_record_id": preventive_record_id,
                "requires_manual_review": requires_manual_review,
            }
            record_audit_event(
                session,
                occurred_at=finished_at,
                action=(
                    "PALWORLD_UPDATE_CHECK"
                    if job.kind == UPDATE_CHECK_JOB_KIND
                    else "PALWORLD_UPDATE"
                ),
                result=AUDIT_RESULT_FAILURE,
                origin=AUDIT_ORIGIN_SYSTEM,
                job_id=job.id,
                target="Palworld",
                details={
                    "error": category,
                    "preventive_backup_record_id": preventive_record_id,
                    "requires_manual_review": requires_manual_review,
                },
            )
            if notify:
                session.add(
                    NotificationEvent(
                        event_type="UPDATE_FAILED",
                        channel="DISCORD",
                        status="PENDING",
                        job_id=job_id,
                    )
                )
        self._append_log(job_id, f"Falha controlada: {category}.")

    def _merge_result(self, job_id: int, values: dict[str, object]) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            job.result = {**dict(job.result or {}), **values}

    def _notification(self, job_id: int, event_type: str) -> None:
        with session_scope(self._session_factory) as session:
            session.add(
                NotificationEvent(
                    event_type=event_type,
                    channel="DISCORD",
                    status="PENDING",
                    job_id=job_id,
                )
            )

    def _append_log(self, job_id: int, message: str) -> None:
        with session_scope(self._session_factory) as session:
            path = session.get_one(Job, job_id).log_path
        if path:
            try:
                self._job_logs.append(path, message)
            except (OSError, ValueError):
                return

    def fail_unexpected(self, job_id: int) -> None:
        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            if job.status not in ACTIVE_JOB_STATUSES:
                return
            is_update = job.kind == UPDATE_JOB_KIND
            requires_manual_review = is_update and job.is_cancellable is False
        self._fail(
            job_id,
            category="UNEXPECTED_UPDATE_FAILURE",
            message="O Update falhou de forma controlada.",
            requires_manual_review=requires_manual_review,
            notify=is_update,
        )


def latest_update_check(session: Session) -> Job | None:
    return session.scalar(
        select(Job).where(Job.kind == UPDATE_CHECK_JOB_KIND).order_by(Job.id.desc()).limit(1)
    )


def latest_update_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job).where(Job.kind == UPDATE_JOB_KIND).order_by(Job.id.desc()).limit(1)
    )


def update_job_view(job: Job) -> UpdateJobView:
    if job.kind not in UPDATE_JOB_KINDS:
        raise ValueError("job não pertence ao Update")
    result = job.result or {}
    available_at = result.get("available_at")
    parsed_available_at: datetime | None = None
    if isinstance(available_at, str):
        try:
            parsed_available_at = datetime.fromisoformat(available_at.replace("Z", "+00:00"))
        except ValueError:
            parsed_available_at = None
    installed = result.get("installed_build_id")
    available = result.get("available_build_id")
    update_available = result.get("update_available")
    disk_critical = result.get("disk_critical")
    preventive = result.get("preventive_backup_record_id")
    manual_review = result.get("requires_manual_review")
    message = result.get("message")
    return UpdateJobView(
        id=job.id,
        kind=job.kind,
        status=job.status,
        step=job.step,
        progress=job.progress,
        is_cancellable=job.is_cancellable and job.status in ACTIVE_JOB_STATUSES,
        installed_build_id=installed if isinstance(installed, str) else None,
        available_build_id=available if isinstance(available, str) else None,
        available_at=parsed_available_at,
        update_available=update_available if isinstance(update_available, bool) else None,
        disk_critical=disk_critical if isinstance(disk_critical, bool) else None,
        preventive_backup_record_id=(
            preventive if isinstance(preventive, int) and not isinstance(preventive, bool) else None
        ),
        requires_manual_review=(
            manual_review is True
            or (job.kind == UPDATE_JOB_KIND and job.step == JOB_STEP_INTERRUPTED)
        ),
        message=message if isinstance(message, str) else None,
    )


def _ensure_no_active_job(session: Session, coordination_key: str) -> None:
    if (
        session.scalar(
            select(Job.id).where(
                Job.coordination_key == coordination_key,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        is not None
    ):
        raise UpdateJobConflictError("Já existe uma operação incompatível em andamento.")


def _flush_job(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as error:
        raise UpdateJobConflictError("Já existe uma operação incompatível em andamento.") from error


def _mark_cancelled(
    session: Session,
    job: Job,
    *,
    result: dict[str, object],
    occurred_at: datetime,
) -> None:
    job.status = JOB_STATUS_CANCELLED
    job.step = JOB_STEP_CANCELLED
    job.progress = 100
    job.is_cancellable = False
    job.finished_at = occurred_at
    job.result = result
    record_audit_event(
        session,
        occurred_at=occurred_at,
        action="PALWORLD_UPDATE",
        result=AUDIT_RESULT_CANCELLED,
        origin=AUDIT_ORIGIN_SYSTEM,
        job_id=job.id,
        target="Palworld",
        details={
            "preventive_backup_record_id": result.get("preventive_backup_record_id"),
            "requires_manual_review": False,
        },
    )


def _disk_critical_gb(session: Session) -> int:
    setting = session.get(AppSetting, DISK_CRITICAL_KEY)
    if setting is None:
        return DEFAULT_DISK_CRITICAL_GB
    value = setting.value
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1024:
        raise ValueError("disk_critical_gb possui valor inválido")
    return value


def _build_result(build: SteamBuildInfo) -> dict[str, object]:
    return {
        "installed_build_id": build.installed_build_id,
        "available_build_id": build.available_build_id,
        "available_at": _datetime_value(build.available_at),
        "update_available": build.update_available,
    }


def _datetime_value(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _required_build_id(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.isdecimal() or not 1 <= len(value) <= 20:
        raise ValueError(f"job possui {key} inválido")
    return value


def _required_int(data: dict[str, object], key: str, *, minimum: int, maximum: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"job possui {key} inválido")
    return value


def _step_log_message(step: str) -> str:
    messages = {
        "CHECKING": "Consulta de versão iniciada.",
        "CHECKING_DISK": "Verificação de espaço livre iniciada.",
        "VERIFYING": "Versão pública verificada novamente sob lock.",
        "PREVENTIVE_BACKUP": "Backup pré-update iniciado.",
        "PREVENTIVE_SAFE_SAVE": "Salvamento seguro solicitado para o backup pré-update.",
        "PREVENTIVE_COPYING_WORLD": "Cópia do mundo para o backup pré-update iniciada.",
        "PREVENTIVE_COPYING_DATABASE": "Snapshot do banco para o backup pré-update iniciado.",
        "PREVENTIVE_BUILDING_MANIFEST": "Manifest do backup pré-update iniciado.",
        "PREVENTIVE_COMPRESSING": "Compactação do backup pré-update iniciada.",
        "PREVENTIVE_VALIDATING": "Validação do backup pré-update iniciada.",
        "PREVENTIVE_PUBLISHING": "Publicação atômica do backup pré-update iniciada.",
        "ASSISTED_SHUTDOWN": "Desligamento assistido iniciado.",
        "UPDATING": "SteamCMD iniciado; cancelamento indisponível.",
        "STARTING": "Inicialização e health check do Palworld iniciados.",
        "VERIFYING_VERSION": "Validação final de versão e logs iniciada.",
    }
    return messages.get(step, "Etapa do Update atualizada.")
