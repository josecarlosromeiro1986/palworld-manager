import logging
import signal
from threading import Event
from types import FrameType

from app.config import Settings

logger = logging.getLogger(__name__)
shutdown_requested = Event()


def _request_shutdown(_signum: int, _frame: FrameType | None) -> None:
    shutdown_requested.set()


def run() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    logger.info(
        "Worker de bootstrap iniciado em %s; a execução de jobs ainda não está implementada.",
        settings.environment.value,
    )
    shutdown_requested.wait()
    logger.info("Worker de bootstrap encerrado.")


if __name__ == "__main__":
    run()
