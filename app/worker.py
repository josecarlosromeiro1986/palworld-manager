import logging
import os
import signal
import socket
from threading import Event
from types import FrameType

from app.config import Settings
from app.db.engine import create_database_engine, create_session_factory
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
    assisted_shutdown, forced_shutdown = create_shutdown_executors(settings, session_factory)
    worker = LifecycleJobWorker(
        session_factory,
        create_lifecycle_executor(settings, session_factory),
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        assisted_shutdown_executor=assisted_shutdown,
        forced_shutdown_executor=forced_shutdown,
    )

    logger.info("Worker iniciado em %s.", settings.environment.value)
    try:
        while not shutdown_requested.is_set():
            processed = worker.process_next()
            if not processed:
                shutdown_requested.wait(1.0)
    finally:
        engine.dispose()
        logger.info("Worker encerrado.")


if __name__ == "__main__":
    run()
