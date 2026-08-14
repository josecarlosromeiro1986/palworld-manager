from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import Job
from app.lifecycle.jobs import (
    claim_next_lifecycle_job,
    execute_lifecycle_job,
    fail_lifecycle_job,
)
from app.lifecycle.service import LifecycleExecutor
from app.shutdown.jobs import (
    ShutdownJobKind,
    claim_next_shutdown_job,
    execute_assisted_shutdown_job,
    execute_forced_shutdown_job,
    fail_shutdown_job,
)
from app.shutdown.service import AssistedShutdownExecutor, ForcedShutdownExecutor


class LifecycleJobWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        executor: LifecycleExecutor,
        *,
        worker_id: str,
        assisted_shutdown_executor: AssistedShutdownExecutor | None = None,
        forced_shutdown_executor: ForcedShutdownExecutor | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("o identificador do worker é obrigatório")
        self._session_factory = session_factory
        self._executor = executor
        self._worker_id = worker_id
        self._assisted_shutdown_executor = assisted_shutdown_executor
        self._forced_shutdown_executor = forced_shutdown_executor

    def process_next(self) -> bool:
        with session_scope(self._session_factory) as session:
            job = claim_next_lifecycle_job(session, self._worker_id)
            if job is None and self._assisted_shutdown_executor is not None:
                job = claim_next_shutdown_job(session, self._worker_id)
            if job is None:
                return False
            job_id = job.id
            job_kind = job.kind

        if job_kind in {kind.value for kind in ShutdownJobKind}:
            try:
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
            except Exception:
                fail_shutdown_job(self._session_factory, job_id)
            return True

        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            try:
                execute_lifecycle_job(session, job, self._executor)
            except Exception:
                fail_lifecycle_job(session, job)
        return True
