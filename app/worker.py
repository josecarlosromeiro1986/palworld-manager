import logging
import os
import signal
import socket
from threading import Event
from types import FrameType

from app.config import Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import Job
from app.jobs.heartbeat import WorkerHeartbeatPublisher
from app.jobs.logs import create_job_log_store
from app.jobs.service import recover_interrupted_jobs
from app.lifecycle.service import create_lifecycle_executor
from app.lifecycle.worker import LifecycleJobWorker
from app.shutdown.service import create_shutdown_executors

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
        removed_logs = job_logs.prune()
        assisted_shutdown, forced_shutdown = create_shutdown_executors(settings, session_factory)
        worker = LifecycleJobWorker(
            session_factory,
            create_lifecycle_executor(settings, session_factory),
            worker_id=worker_id,
            assisted_shutdown_executor=assisted_shutdown,
            forced_shutdown_executor=forced_shutdown,
            job_logs=job_logs,
        )
        logger.info(
            "Worker iniciado em %s; %d job(s) recuperado(s), %d log(s) expirado(s) removido(s).",
            settings.environment.value,
            len(interrupted_jobs),
            removed_logs,
        )
        while not shutdown_requested.is_set():
            processed = worker.process_next()
            if not processed:
                shutdown_requested.wait(1.0)
    finally:
        heartbeat.stop()
        engine.dispose()
        logger.info("Worker encerrado.")


if __name__ == "__main__":
    run()
