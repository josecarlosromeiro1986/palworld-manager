from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import create_administrator
from app.backups.service import LocalBackupService
from app.backups.source import FakeBackupPayloadSource
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import (
    AppSetting,
    AuditEvent,
    BackupRecord,
    Job,
    MaintenanceLock,
    NotificationEvent,
)
from app.integrations.palworld_rest import (
    FakePalworldRestClient,
    PalworldRestErrorKind,
)
from app.jobs.logs import MemoryJobLogStore
from app.jobs.service import GLOBAL_MAINTENANCE_LOCK, claim_next_job, recover_interrupted_jobs
from app.lifecycle.fake import FAKE_PALWORLD_ACTIVE_KEY, PersistentFakePalworldEnvironment
from app.lifecycle.service import create_lifecycle_executor
from app.lifecycle.worker import LifecycleJobWorker
from app.logs.service import LogCategory, LogEntry
from app.shutdown.service import create_shutdown_executors
from app.updates.jobs import (
    UPDATE_CHECK_JOB_KIND,
    UPDATE_JOB_KIND,
    UpdateJobConflictError,
    UpdateJobExecutor,
    enqueue_update,
    enqueue_update_check,
    request_update_cancel,
)
from app.updates.service import FakeDiskSpaceSource, FakeSteamCmdGateway, SteamCmdError


@dataclass
class UpdateContext:
    engine: Engine
    steamcmd: FakeSteamCmdGateway
    disk: FakeDiskSpaceSource
    rest: FakePalworldRestClient
    logs: "ControlledLogs"
    worker: LifecycleJobWorker
    user_id: int


class ControlledLogs:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int | None]] = []

    def history(self, limit: int) -> list[LogEntry]:
        assert limit == 100
        return [
            LogEntry(
                f"test:{index}",
                datetime.now(UTC) + timedelta(seconds=1),
                message,
                LogCategory.ERROR,
                priority,
            )
            for index, (message, priority) in enumerate(self.messages, start=1)
        ]

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]:
        del after_cursor
        return iter(())


@pytest.fixture
def update_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UpdateContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        user_id = create_administrator(session, "admin", "fake-login-password").id
        session.merge(AppSetting(key="assisted_shutdown_default_minutes", value=0))
    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    rest = FakePalworldRestClient()
    backup_health = PersistentFakePalworldEnvironment(factory)
    backup_health.start()
    backup_service = LocalBackupService(
        manager_database=database_path,
        session_factory=factory,
        payload_source=FakeBackupPayloadSource(rest),
        palworld_health=backup_health,
    )
    lifecycle = create_lifecycle_executor(settings, factory)
    assisted, forced = create_shutdown_executors(settings, factory)
    steamcmd = FakeSteamCmdGateway()
    disk = FakeDiskSpaceSource()
    logs = MemoryJobLogStore()
    palworld_logs = ControlledLogs()
    worker = LifecycleJobWorker(
        factory,
        lifecycle,
        worker_id="update-worker",
        assisted_shutdown_executor=assisted,
        forced_shutdown_executor=forced,
        job_logs=logs,
        update_executor=UpdateJobExecutor(
            factory,
            steamcmd,
            disk,
            backup_service,
            assisted,
            lifecycle,
            palworld_logs,
            job_logs=logs,
        ),
    )
    yield UpdateContext(engine, steamcmd, disk, rest, palworld_logs, worker, user_id)
    engine.dispose()


def _factory(context: UpdateContext) -> sessionmaker[Session]:
    return create_session_factory(context.engine)


def _check_then_enqueue_update(context: UpdateContext) -> int:
    factory = _factory(context)
    with session_scope(factory) as session:
        enqueue_update_check(session, user_id=context.user_id)
    assert context.worker.process_next() is True
    with session_scope(factory) as session:
        job = enqueue_update(
            session,
            confirmation="ATUALIZAR",
            user_id=context.user_id,
        )
        return job.id


def test_manual_update_creates_valid_preventive_backup_and_finishes_online(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)

    assert update_context.worker.process_next() is True

    with session_scope(_factory(update_context)) as session:
        job = session.get_one(Job, job_id)
        backup = session.scalar(select(BackupRecord).where(BackupRecord.job_id == job_id))
        notifications = list(
            session.scalars(select(NotificationEvent).where(NotificationEvent.job_id == job_id))
        )
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.job_id == job_id,
                AuditEvent.action == "PALWORLD_UPDATE",
                AuditEvent.result == "SUCCESS",
            )
        )
        assert job.kind == UPDATE_JOB_KIND
        assert job.status == "SUCCEEDED"
        assert job.result is not None
        assert job.result["installed_build_id"] == "10000002"
        assert job.result["requires_manual_review"] is False
        assert backup is not None
        assert backup.status == "VALID"
        assert backup.sha256 is not None
        assert [event.event_type for event in notifications] == ["UPDATE_COMPLETED"]
        assert audit is not None
        assert update_context.steamcmd.update_calls == 1


@pytest.mark.parametrize(
    ("message", "priority"),
    [
        ("LogHttp: access-control-expose-headers: x-sentry-error", 6),
        ("palworld.service: Main process exited, code=exited, status=143/n/a", 3),
    ],
)
def test_update_ignores_visual_errors_that_are_not_operationally_critical(
    update_context: UpdateContext,
    message: str,
    priority: int,
) -> None:
    update_context.logs.messages = [(message, priority)]
    job_id = _check_then_enqueue_update(update_context)

    assert update_context.worker.process_next() is True

    with session_scope(_factory(update_context)) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "SUCCEEDED"
        assert job.result is not None
        assert job.result["requires_manual_review"] is False


def test_update_aborts_before_backup_when_disk_is_critical(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)
    update_context.disk.available_bytes = 9 * 1024**3

    assert update_context.worker.process_next() is True

    with session_scope(_factory(update_context)) as session:
        job = session.get_one(Job, job_id)
        backups = list(session.scalars(select(BackupRecord).where(BackupRecord.job_id == job_id)))
        events = list(
            session.scalars(
                select(NotificationEvent)
                .where(NotificationEvent.job_id == job_id)
                .order_by(NotificationEvent.id)
            )
        )
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "DISK_CRITICAL"
        assert job.result["requires_manual_review"] is False
        assert backups == []
        assert [event.event_type for event in events] == ["DISK_CRITICAL", "UPDATE_FAILED"]
        assert update_context.steamcmd.update_calls == 0


def test_steamcmd_failure_preserves_backup_and_requires_manual_review(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)
    update_context.steamcmd.update_error = SteamCmdError("fake-secret-must-not-leak")

    assert update_context.worker.process_next() is True

    with session_scope(_factory(update_context)) as session:
        job = session.get_one(Job, job_id)
        backup = session.scalar(select(BackupRecord).where(BackupRecord.job_id == job_id))
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.job_id == job_id,
                AuditEvent.action == "PALWORLD_UPDATE",
                AuditEvent.result == "FAILURE",
            )
        )
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "STEAMCMD_FAILED"
        assert job.result["requires_manual_review"] is True
        assert "fake-secret" not in str(job.result)
        assert backup is not None
        assert backup.status == "VALID"
        assert audit is not None
        assert "fake-secret" not in str(audit.details)
        fake_state = session.get(AppSetting, FAKE_PALWORLD_ACTIVE_KEY)
        assert fake_state is not None
        assert fake_state.value is False


def test_safe_save_failure_prevents_update_and_valid_backup_record(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)
    update_context.rest.set_error(PalworldRestErrorKind.TIMEOUT)

    assert update_context.worker.process_next() is True

    with session_scope(_factory(update_context)) as session:
        job = session.get_one(Job, job_id)
        records = list(session.scalars(select(BackupRecord).where(BackupRecord.job_id == job_id)))
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "PREVENTIVE_BACKUP_FAILED"
        assert job.result["requires_manual_review"] is False
        assert records == []
        assert update_context.steamcmd.update_calls == 0


def test_pending_update_can_be_cancelled_but_critical_update_cannot(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)
    factory = _factory(update_context)
    with session_scope(factory) as session:
        assert request_update_cancel(session, job_id, user_id=update_context.user_id) is True
    with session_scope(factory) as session:
        cancelled = session.get_one(Job, job_id)
        assert cancelled.status == "CANCELLED"
        assert cancelled.is_cancellable is False

        critical = Job(
            kind=UPDATE_JOB_KIND,
            status="RUNNING",
            step="UPDATING",
            progress=72,
            is_cancellable=False,
            requires_maintenance_lock=True,
            coordination_key="PALWORLD_LIFECYCLE",
            result={},
        )
        session.add(critical)
        session.flush()
        assert request_update_cancel(session, critical.id, user_id=update_context.user_id) is False


def test_check_is_manual_read_only_and_does_not_take_maintenance_lock(
    update_context: UpdateContext,
) -> None:
    with session_scope(_factory(update_context)) as session:
        job = enqueue_update_check(session, user_id=update_context.user_id)
        assert job.kind == UPDATE_CHECK_JOB_KIND
        assert job.requires_maintenance_lock is False
        assert job.status == "PENDING"

    assert update_context.worker.process_next() is True

    with session_scope(_factory(update_context)) as session:
        checked = session.scalar(select(Job).where(Job.kind == UPDATE_CHECK_JOB_KIND))
        assert checked is not None
        assert checked.status == "SUCCEEDED"
        assert checked.result is not None
        assert checked.result["update_available"] is True
        assert update_context.steamcmd.update_calls == 0


def test_update_requires_global_lock_and_duplicate_request_is_rejected(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)
    with session_scope(_factory(update_context)) as session:
        job = session.get_one(Job, job_id)
        assert job.requires_maintenance_lock is True
        assert job.coordination_key == "PALWORLD_LIFECYCLE"
        with pytest.raises(UpdateJobConflictError, match="operação incompatível"):
            enqueue_update(
                session,
                confirmation="ATUALIZAR",
                user_id=update_context.user_id,
            )


def test_interrupted_update_is_not_requeued_and_releases_maintenance_lock(
    update_context: UpdateContext,
) -> None:
    job_id = _check_then_enqueue_update(update_context)
    factory = _factory(update_context)
    with session_scope(factory) as session:
        claimed = claim_next_job(session, "worker-before-crash", (UPDATE_JOB_KIND,))
        assert claimed is not None
        assert claimed.id == job_id
        assert session.get(MaintenanceLock, GLOBAL_MAINTENANCE_LOCK) is not None
    with session_scope(factory) as session:
        interrupted = recover_interrupted_jobs(session)
        assert [item.id for item in interrupted] == [job_id]

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "INTERRUPTED"
        assert job.is_cancellable is False
        assert job.result is not None
        assert job.result["requires_manual_review"] is True
        assert session.get(MaintenanceLock, GLOBAL_MAINTENANCE_LOCK) is None
        assert claim_next_job(session, "worker-after-crash", (UPDATE_JOB_KIND,)) is None
