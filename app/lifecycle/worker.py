import logging

from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import Job
from app.jobs.logs import JobLogStore, MemoryJobLogStore
from app.jobs.service import claim_next_job, release_maintenance_lock
from app.lifecycle.jobs import (
    execute_lifecycle_job,
    fail_lifecycle_job,
    lifecycle_job_kind,
)
from app.lifecycle.service import LifecycleAction, LifecycleExecutor
from app.shutdown.jobs import (
    ShutdownJobKind,
    execute_assisted_shutdown_job,
    execute_forced_shutdown_job,
    fail_shutdown_job,
)
from app.shutdown.service import AssistedShutdownExecutor, ForcedShutdownExecutor

logger = logging.getLogger(__name__)


class LifecycleJobWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        executor: LifecycleExecutor,
        *,
        worker_id: str,
        assisted_shutdown_executor: AssistedShutdownExecutor | None = None,
        forced_shutdown_executor: ForcedShutdownExecutor | None = None,
        job_logs: JobLogStore | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("o identificador do worker é obrigatório")
        self._session_factory = session_factory
        self._executor = executor
        self._worker_id = worker_id
        self._assisted_shutdown_executor = assisted_shutdown_executor
        self._forced_shutdown_executor = forced_shutdown_executor
        self._job_logs = job_logs or MemoryJobLogStore()

    @property
    def _supported_kinds(self) -> tuple[str, ...]:
        lifecycle = tuple(lifecycle_job_kind(action) for action in LifecycleAction)
        shutdown = (
            tuple(kind.value for kind in ShutdownJobKind)
            if self._assisted_shutdown_executor is not None
            and self._forced_shutdown_executor is not None
            else ()
        )
        return lifecycle + shutdown

    def process_next(self) -> bool:
        with session_scope(self._session_factory) as session:
            job = claim_next_job(session, self._worker_id, self._supported_kinds)
            if job is None:
                return False
            job.log_path = self._job_logs.create(job.id, job.kind)
            job_id = job.id
            job_kind = job.kind
            job_log_path = job.log_path

        try:
            self._append_log(job_log_path, "Execução iniciada.")
            if job_kind in {kind.value for kind in ShutdownJobKind}:
                if job_kind == ShutdownJobKind.ASSISTED.value:
                    assert self._assisted_shutdown_executor is not None
                    execute_assisted_shutdown_job(
                        self._session_factory, job_id, self._assisted_shutdown_executor
                    )
                else:
                    assert self._forced_shutdown_executor is not None
                    execute_forced_shutdown_job(
                        self._session_factory, job_id, self._forced_shutdown_executor
                    )
            else:
                with session_scope(self._session_factory) as session:
                    job = session.get_one(Job, job_id)
                    execute_lifecycle_job(session, job, self._executor)
        except Exception:
            self._append_log(job_log_path, "Execução falhou de forma inesperada.")
            if job_kind in {kind.value for kind in ShutdownJobKind}:
                fail_shutdown_job(self._session_factory, job_id)
            else:
                with session_scope(self._session_factory) as session:
                    job = session.get_one(Job, job_id)
                    fail_lifecycle_job(session, job)
        else:
            self._append_log(job_log_path, "Execução finalizada.")
        finally:
            with session_scope(self._session_factory) as session:
                release_maintenance_lock(session, job_id)
        return True

    def _append_log(self, log_path: str, message: str) -> None:
        try:
            self._job_logs.append(log_path, message)
        except (OSError, ValueError):
            logger.error("Não foi possível atualizar o log textual do job.")
