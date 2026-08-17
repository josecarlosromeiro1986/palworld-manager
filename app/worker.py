import logging
import os
import signal
import socket
from threading import Event
from types import FrameType

from sqlalchemy import select

from app.backups.drive_jobs import DRIVE_UPLOAD_JOB_KIND, DriveJobExecutor
from app.backups.drive_service import DriveTransferService
from app.backups.jobs import LOCAL_BACKUP_JOB_KIND, LocalBackupJobExecutor
from app.backups.scheduler import schedule_daily_backup
from app.backups.service import LocalBackupService
from app.backups.source import create_backup_payload_source
from app.config import Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import Job, NotificationEvent
from app.integrations.google_drive import GoogleDriveError, create_google_drive_storage
from app.integrations.palworld_rest import create_palworld_rest_client
from app.jobs.heartbeat import WorkerHeartbeatPublisher
from app.jobs.logs import create_job_log_store
from app.jobs.service import TERMINAL_JOB_STATUSES, recover_interrupted_jobs
from app.lifecycle.service import create_lifecycle_executor
from app.lifecycle.worker import LifecycleJobWorker
from app.logs.service import create_palworld_log_source
from app.restores.jobs import LocalRestoreJobExecutor
from app.restores.service import LocalRestoreService, create_restore_target
from app.shutdown.service import create_shutdown_executors
from app.updates.jobs import UPDATE_JOB_KIND, UpdateJobExecutor
from app.updates.service import create_disk_space_source, create_steamcmd_gateway

logger = logging.getLogger(__name__)
shutdown_requested = Event()


def _request_shutdown(_signum: int, _frame: FrameType | None) -> None:
    shutdown_requested.set()


def run() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    shutdown_requested.clear()
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    engine = create_database_engine(settings.manager_database)
    session_factory = create_session_factory(engine)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    job_logs = create_job_log_store(settings.manager_database)
    heartbeat = WorkerHeartbeatPublisher(session_factory, worker_id)
    try:
        heartbeat.start()
        with session_scope(session_factory) as session:
            interrupted_jobs = recover_interrupted_jobs(session)
            for interrupted in interrupted_jobs:
                log_path = interrupted.log_path
                if log_path is None:
                    log_path = job_logs.create(interrupted.id, interrupted.kind)
                    session.get_one(Job, interrupted.id).log_path = log_path
                job_logs.append(
                    log_path,
                    "Worker reiniciado; job interrompido e bloqueado para revisão manual.",
                )
                if interrupted.kind == UPDATE_JOB_KIND:
                    session.add(
                        NotificationEvent(
                            event_type="UPDATE_INTERRUPTED",
                            channel="DISCORD",
                            status="PENDING",
                            job_id=interrupted.id,
                        )
                    )
        removed_logs = job_logs.prune()
        rest_client = create_palworld_rest_client(settings)
        backup_service = LocalBackupService(
            manager_database=settings.manager_database,
            session_factory=session_factory,
            payload_source=create_backup_payload_source(settings, rest_client),
        )
        removed_temporary_backups = backup_service.cleanup_temporary_artifacts()
        removed_interrupted_backups = backup_service.cleanup_interrupted_artifacts(
            tuple(job.id for job in interrupted_jobs if job.kind == LOCAL_BACKUP_JOB_KIND)
        )
        drive_storage = create_google_drive_storage(settings)
        drive_service = DriveTransferService(
            manager_database=settings.manager_database,
            local_backups=backup_service,
            storage=drive_storage,
        )
        removed_drive_temporary = drive_service.cleanup_temporary_artifacts()
        with session_scope(session_factory) as session:
            completed_drive_upload_ids = tuple(
                session.scalars(
                    select(Job.id).where(
                        Job.kind == DRIVE_UPLOAD_JOB_KIND,
                        Job.status.in_(TERMINAL_JOB_STATUSES),
                    )
                )
            )
        try:
            removed_remote_temporary = (
                drive_service.cleanup_interrupted_uploads(completed_drive_upload_ids)
                if completed_drive_upload_ids
                else 0
            )
        except GoogleDriveError:
            removed_remote_temporary = 0
            logger.warning("Não foi possível verificar temporários remotos interrompidos.")
        assisted_shutdown, forced_shutdown = create_shutdown_executors(settings, session_factory)
        lifecycle_executor = create_lifecycle_executor(settings, session_factory)
        restore_service = LocalRestoreService(
            manager_database=settings.manager_database,
            backup_service=backup_service,
            target=create_restore_target(settings),
        )
        worker = LifecycleJobWorker(
            session_factory,
            lifecycle_executor,
            worker_id=worker_id,
            assisted_shutdown_executor=assisted_shutdown,
            forced_shutdown_executor=forced_shutdown,
            job_logs=job_logs,
            backup_executor=LocalBackupJobExecutor(
                session_factory,
                backup_service,
                automatic_drive_uploads=True,
            ),
            restore_executor=LocalRestoreJobExecutor(
                session_factory,
                restore_service,
                backup_service,
                lifecycle_executor,
                create_palworld_log_source(settings),
                drive_service,
            ),
            drive_executor=DriveJobExecutor(session_factory, drive_service),
            update_executor=UpdateJobExecutor(
                session_factory,
                create_steamcmd_gateway(settings),
                create_disk_space_source(settings),
                backup_service,
                assisted_shutdown,
                lifecycle_executor,
                create_palworld_log_source(settings),
                job_logs=job_logs,
            ),
        )
        logger.info(
            "Worker iniciado em %s; %d job(s) recuperado(s), %d log(s) expirado(s) removido(s).",
            settings.environment.value,
            len(interrupted_jobs),
            removed_logs,
        )
        if removed_temporary_backups:
            logger.info(
                "%d área(s) temporária(s) de backup interrompido removida(s).",
                removed_temporary_backups,
            )
        if removed_interrupted_backups:
            logger.info(
                "%d artefato(s) de backup interrompido removido(s).",
                removed_interrupted_backups,
            )
        if removed_drive_temporary:
            logger.info(
                "%d área(s) temporária(s) de download remoto removida(s).",
                removed_drive_temporary,
            )
        if removed_remote_temporary:
            logger.info(
                "%d upload(s) remoto(s) temporário(s) interrompido(s) removido(s).",
                removed_remote_temporary,
            )
        while not shutdown_requested.is_set():
            with session_scope(session_factory) as session:
                schedule_daily_backup(session)
            processed = worker.process_next()
            if not processed:
                shutdown_requested.wait(1.0)
    finally:
        heartbeat.stop()
        engine.dispose()
        logger.info("Worker encerrado.")


if __name__ == "__main__":
    run()
