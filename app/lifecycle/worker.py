from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import Job
from app.lifecycle.jobs import (
    claim_next_lifecycle_job,
    execute_lifecycle_job,
    fail_lifecycle_job,
)
from app.lifecycle.service import LifecycleExecutor


class LifecycleJobWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        executor: LifecycleExecutor,
        *,
        worker_id: str,
    ) -> None:
        if not worker_id:
            raise ValueError("o identificador do worker é obrigatório")
        self._session_factory = session_factory
        self._executor = executor
        self._worker_id = worker_id

    def process_next(self) -> bool:
        with session_scope(self._session_factory) as session:
            job = claim_next_lifecycle_job(session, self._worker_id)
            if job is None:
                return False
            job_id = job.id

        with session_scope(self._session_factory) as session:
            job = session.get_one(Job, job_id)
            try:
                execute_lifecycle_job(session, job, self._executor)
            except Exception:
                fail_lifecycle_job(session, job)
        return True
