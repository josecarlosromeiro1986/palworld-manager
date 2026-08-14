import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from sqlalchemy import delete, or_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import WorkerHeartbeat

WORKER_HEARTBEAT_KEY = "PRIMARY"
HEARTBEAT_INTERVAL_SECONDS = 10.0
WORKER_LEASE_TIMEOUT_SECONDS = 30.0

logger = logging.getLogger(__name__)


class WorkerAlreadyRunningError(RuntimeError):
    """Outro processo do worker ainda possui um lease recente."""


def record_worker_start(
    session: Session,
    worker_id: str,
    *,
    started_at: datetime | None = None,
) -> None:
    if not worker_id:
        raise ValueError("o identificador do worker é obrigatório")
    now = started_at or datetime.now(UTC)
    stale_before = now - timedelta(seconds=WORKER_LEASE_TIMEOUT_SECONDS)
    statement = insert(WorkerHeartbeat).values(
        key=WORKER_HEARTBEAT_KEY,
        worker_id=worker_id,
        started_at=now,
        heartbeat_at=now,
    )
    claimed = session.scalar(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.key],
            set_={
                "worker_id": worker_id,
                "started_at": now,
                "heartbeat_at": now,
            },
            where=or_(
                WorkerHeartbeat.worker_id == worker_id,
                WorkerHeartbeat.heartbeat_at <= stale_before,
            ),
        ).returning(WorkerHeartbeat.key)
    )
    if claimed is None:
        raise WorkerAlreadyRunningError("já existe um worker com heartbeat recente")


def record_worker_heartbeat(
    session: Session,
    worker_id: str,
    *,
    heartbeat_at: datetime | None = None,
) -> bool:
    now = heartbeat_at or datetime.now(UTC)
    heartbeat = session.get(WorkerHeartbeat, WORKER_HEARTBEAT_KEY)
    if heartbeat is None or heartbeat.worker_id != worker_id:
        return False
    heartbeat.heartbeat_at = now
    return True


def release_worker_lease(session: Session, worker_id: str) -> None:
    session.execute(
        delete(WorkerHeartbeat).where(
            WorkerHeartbeat.key == WORKER_HEARTBEAT_KEY,
            WorkerHeartbeat.worker_id == worker_id,
        )
    )


class WorkerHeartbeatPublisher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        worker_id: str,
        *,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("o intervalo do heartbeat deve ser positivo")
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._interval_seconds = interval_seconds
        self._now = now
        self._stop_requested = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("o heartbeat já foi iniciado")
        with session_scope(self._session_factory) as session:
            record_worker_start(session, self._worker_id, started_at=self._now())
        self._thread = Thread(
            target=self._run,
            name="worker-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1)
            self._thread = None
        with session_scope(self._session_factory) as session:
            release_worker_lease(session, self._worker_id)

    def _run(self) -> None:
        while not self._stop_requested.wait(self._interval_seconds):
            try:
                with session_scope(self._session_factory) as session:
                    updated = record_worker_heartbeat(
                        session,
                        self._worker_id,
                        heartbeat_at=self._now(),
                    )
                if not updated:
                    logger.error("Heartbeat rejeitado porque a identidade do worker mudou.")
                    return
            except Exception:
                logger.exception("Falha ao persistir heartbeat do worker.")
