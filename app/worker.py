import logging
import signal
from threading import Event
from types import FrameType

logger = logging.getLogger(__name__)
shutdown_requested = Event()


def _request_shutdown(_signum: int, _frame: FrameType | None) -> None:
    shutdown_requested.set()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    logger.info("Worker de bootstrap iniciado; a execução de jobs ainda não está implementada.")
    shutdown_requested.wait()
    logger.info("Worker de bootstrap encerrado.")


if __name__ == "__main__":
    run()
