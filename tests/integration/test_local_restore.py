from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.backups.jobs import LocalBackupJobExecutor, enqueue_local_backup
from app.backups.service import LocalBackupService
from app.backups.source import FakeBackupPayloadSource
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, AuditEvent, BackupRecord, Job, MaintenanceLock, User
from app.health.palworld import PalworldHealthState
from app.integrations.palworld_rest import FakePalworldRestClient, PalworldRestErrorKind
from app.jobs.logs import MemoryJobLogStore
from app.jobs.service import GLOBAL_MAINTENANCE_LOCK, recover_interrupted_jobs
from app.lifecycle.service import (
    LifecycleAction,
    LifecycleOutcome,
    LifecycleResult,
)
from app.lifecycle.worker import LifecycleJobWorker
from app.logs.service import LogCategory, LogEntry
from app.main import create_app
from app.palworld_settings.storage import (
    FakePalworldSettingsStorage,
    SettingsStorageErrorKind,
)
from app.restores.jobs import (
    LocalRestoreJobExecutor,
    RestoreJobConflictError,
    RestoreRequestError,
    enqueue_local_restore,
)
from app.restores.service import FakeRestoreTarget, LocalRestoreService


class RecordingLifecycle:
    def __init__(self) -> None:
        self.actions: list[LifecycleAction] = []
        self.fail_action: LifecycleAction | None = None

    def execute(self, action: LifecycleAction, timeout_seconds: int) -> LifecycleResult:
        assert timeout_seconds > 0
        self.actions.append(action)
        failed = action is self.fail_action
        return LifecycleResult(
            LifecycleOutcome.FAILED if failed else LifecycleOutcome.SUCCEEDED,
            (
                PalworldHealthState.FAILURE
                if failed
                else PalworldHealthState.OFFLINE
                if action is LifecycleAction.STOP
                else PalworldHealthState.ONLINE
            ),
            timed_out=failed,
        )


class ControlledLogs:
    def __init__(self) -> None:
        self.critical = False

    def history(self, limit: int) -> list[LogEntry]:
        assert limit == 100
        if not self.critical:
            return []
        return [
            LogEntry(
                "test:1",
                datetime.now(UTC) + timedelta(seconds=1),
                "erro crítico simulado sem dados sensíveis",
                LogCategory.ERROR,
            )
        ]

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]:
        del after_cursor
        return iter(())


@dataclass
class RestoreContext:
    engine: Engine
    database_path: Path
    rest: FakePalworldRestClient
    backup_service: LocalBackupService
    restore_service: LocalRestoreService
    target: FakeRestoreTarget
    lifecycle: RecordingLifecycle
    logs: ControlledLogs
    worker: LifecycleJobWorker
    source_record_id: int


@pytest.fixture
def restore_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RestoreContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "fake-login-password")
    rest = FakePalworldRestClient()
    backup_service = LocalBackupService(
        manager_database=database_path,
        session_factory=factory,
        payload_source=FakeBackupPayloadSource(rest),
    )
    storage = FakePalworldSettingsStorage(
        "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=("
        'ServerName="Atual",AdminPassword="fake-current-admin",'
        'ServerPassword="fake-current-server",FutureSetting=(Mode="Keep,Me"))\n'
    )
    target = FakeRestoreTarget(storage)
    lifecycle = RecordingLifecycle()
    logs = ControlledLogs()
    restore_service = LocalRestoreService(
        manager_database=database_path,
        backup_service=backup_service,
        target=target,
    )
    worker = LifecycleJobWorker(
        factory,
        lifecycle,
        worker_id="restore-worker",
        backup_executor=LocalBackupJobExecutor(factory, backup_service),
        restore_executor=LocalRestoreJobExecutor(
            factory,
            restore_service,
            backup_service,
            lifecycle,
            logs,
        ),
        job_logs=MemoryJobLogStore(),
    )
    with session_scope(factory) as session:
        backup_job = enqueue_local_backup(session, user_id=None, trigger="MANUAL")
        backup_job_id = backup_job.id
    assert worker.process_next()
    with session_scope(factory) as session:
        source_record = session.scalar(
            select(BackupRecord).where(BackupRecord.job_id == backup_job_id)
        )
        assert source_record is not None
        source_record_id = source_record.id
        session.add(AppSetting(key="development_note", value="keep-current-manager-state"))
    yield RestoreContext(
        engine,
        database_path,
        rest,
        backup_service,
        restore_service,
        target,
        lifecycle,
        logs,
        worker,
        source_record_id,
    )
    engine.dispose()


def _enqueue_restore(context: RestoreContext) -> int:
    factory = create_session_factory(context.engine)
    with session_scope(factory) as session:
        job = enqueue_local_restore(
            session,
            backup_record_id=context.source_record_id,
            confirmation="RESTAURAR",
            user_id=1,
        )
        return job.id


def test_valid_restore_preserves_manager_state_secrets_and_creates_preventive_backup(
    restore_context: RestoreContext,
) -> None:
    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        users_before = session.scalar(select(func.count()).select_from(User))
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        records = tuple(session.scalars(select(BackupRecord).order_by(BackupRecord.id)))
        audits = tuple(session.scalars(select(AuditEvent).where(AuditEvent.job_id == job_id)))
        assert job.status == "SUCCEEDED"
        assert job.is_cancellable is False
        assert job.result is not None
        assert job.result["final_state"] == "ONLINE"
        assert job.result["requires_manual_review"] is False
        assert len(records) == 2
        manager_setting = session.get(AppSetting, "development_note")
        assert manager_setting is not None
        assert manager_setting.value == "keep-current-manager-state"
        assert session.scalar(select(func.count()).select_from(User)) == users_before
        assert {event.action for event in audits} == {
            "RESTORE_REQUESTED",
            "BACKUP",
            "RESTORE",
        }
    assert restore_context.lifecycle.actions == [LifecycleAction.STOP, LifecycleAction.START]
    assert restore_context.rest.save_requests == 2
    assert any(
        path.endswith("Players/00000000000000000000000000000001.sav")
        for path in restore_context.target.world_files
    )
    storage = restore_context.target.storage
    assert isinstance(storage, FakePalworldSettingsStorage)
    assert 'ServerName="Fake"' in storage.content
    assert 'AdminPassword="fake-current-admin"' in storage.content
    assert 'ServerPassword="fake-current-server"' in storage.content
    assert 'FutureSetting=(Mode="Keep,Me")' in storage.content
    assert list((restore_context.database_path.parent / "tmp/restores").glob("job-*")) == []


def test_external_sha_failure_happens_before_preventive_backup_stop_or_world_change(
    restore_context: RestoreContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_world = dict(restore_context.target.world_files)
    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        session.get_one(BackupRecord, restore_context.source_record_id).sha256 = "0" * 64
    monkeypatch.setattr(
        "app.restores.service.validate_archive",
        lambda _path: pytest.fail("a validação interna não pode anteceder o SHA-256 externo"),
    )
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "ARCHIVE_SHA256_MISMATCH"
        assert job.result["requires_manual_review"] is False
        assert session.scalar(select(func.count()).select_from(BackupRecord)) == 1
    assert restore_context.lifecycle.actions == []
    assert restore_context.rest.save_requests == 1
    assert restore_context.target.apply_calls == 0
    assert restore_context.target.world_files == original_world


@pytest.mark.parametrize(
    "error_kind",
    [
        SettingsStorageErrorKind.NOT_FOUND,
        SettingsStorageErrorKind.IO,
        SettingsStorageErrorKind.INVALID_FILE,
    ],
)
def test_current_settings_failure_is_prevalidated_before_stop(
    restore_context: RestoreContext,
    error_kind: SettingsStorageErrorKind,
) -> None:
    storage = restore_context.target.storage
    assert isinstance(storage, FakePalworldSettingsStorage)
    storage.set_error(error_kind)
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "CURRENT_SETTINGS_INVALID"
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.job_id == job_id, AuditEvent.action == "RESTORE")
        )
        assert audit is not None
        assert "fake-current-admin" not in str(audit.details)
    assert restore_context.lifecycle.actions == []
    assert restore_context.rest.save_requests == 1


def test_insufficient_space_fails_before_preventive_backup_and_stop(
    restore_context: RestoreContext,
) -> None:
    restore_context.target.available_bytes = 0
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.result is not None
        assert job.result["error"] == "DISK_SPACE_INSUFFICIENT"
    assert restore_context.lifecycle.actions == []
    assert restore_context.rest.save_requests == 1


def test_preventive_safe_save_failure_stops_restore_before_palworld_stop(
    restore_context: RestoreContext,
) -> None:
    restore_context.rest.set_error(PalworldRestErrorKind.TIMEOUT)
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "PREVENTIVE_BACKUP_FAILED"
        assert session.scalar(select(func.count()).select_from(BackupRecord)) == 1
    assert restore_context.lifecycle.actions == []
    assert restore_context.target.apply_calls == 0


def test_stop_failure_preserves_preventive_backup_without_changing_world(
    restore_context: RestoreContext,
) -> None:
    original_world = dict(restore_context.target.world_files)
    restore_context.lifecycle.fail_action = LifecycleAction.STOP
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "STOP_FAILED"
        assert job.result["preventive_backup_record_id"] is not None
        assert job.result["requires_manual_review"] is False
        assert session.scalar(select(func.count()).select_from(BackupRecord)) == 2
    assert restore_context.target.apply_calls == 0
    assert restore_context.target.world_files == original_world


@pytest.mark.parametrize("failure", ["start", "critical_log"])
def test_post_apply_failure_has_no_rollback_and_requires_manual_review(
    restore_context: RestoreContext,
    failure: str,
) -> None:
    if failure == "start":
        restore_context.lifecycle.fail_action = LifecycleAction.START
    else:
        restore_context.logs.critical = True
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["requires_manual_review"] is True
        assert job.result["preventive_backup_record_id"] is not None
    assert restore_context.target.apply_calls == 1
    assert any(path.endswith("Level.sav") for path in restore_context.target.world_files)


def test_restore_is_non_cancellable_locked_and_duplicate_is_rejected(
    restore_context: RestoreContext,
) -> None:
    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        first = enqueue_local_restore(
            session,
            backup_record_id=restore_context.source_record_id,
            confirmation="RESTAURAR",
            user_id=1,
        )
        assert first.is_cancellable is False
        assert first.requires_maintenance_lock is True
        with pytest.raises(RestoreJobConflictError):
            enqueue_local_restore(
                session,
                backup_record_id=restore_context.source_record_id,
                confirmation="RESTAURAR",
                user_id=1,
            )
        with pytest.raises(RestoreRequestError):
            enqueue_local_restore(
                session,
                backup_record_id=restore_context.source_record_id,
                confirmation="restaurar",
                user_id=1,
            )


def test_incompatible_maintenance_lock_prevents_restore_execution(
    restore_context: RestoreContext,
) -> None:
    job_id = _enqueue_restore(restore_context)
    factory = create_session_factory(restore_context.engine)
    with session_scope(factory) as session:
        incompatible = Job(
            kind="PALWORLD_RESTART",
            status="RUNNING",
            requires_maintenance_lock=True,
            coordination_key="PALWORLD_LIFECYCLE",
        )
        session.add(incompatible)
        session.flush()
        session.add(
            MaintenanceLock(
                key=GLOBAL_MAINTENANCE_LOCK,
                job_id=incompatible.id,
                worker_id="other-worker",
                acquired_at=datetime.now(UTC),
            )
        )

    assert restore_context.worker.process_next() is False

    with session_scope(factory) as session:
        assert session.get_one(Job, job_id).status == "PENDING"
    assert restore_context.lifecycle.actions == []


def test_restore_retention_keeps_exactly_three_managed_backups_and_external_file(
    restore_context: RestoreContext,
) -> None:
    factory = create_session_factory(restore_context.engine)
    for _ in range(2):
        with session_scope(factory) as session:
            enqueue_local_backup(session, user_id=None, trigger="MANUAL")
        assert restore_context.worker.process_next()
    backups_directory = restore_context.database_path.parent / "backups"
    external = backups_directory / "arquivo-do-usuario.tar.gz"
    external.write_bytes(b"preservar")
    job_id = _enqueue_restore(restore_context)

    assert restore_context.worker.process_next()

    with session_scope(factory) as session:
        records = tuple(session.scalars(select(BackupRecord)))
        assert len(records) == 3
        assert any(record.id == restore_context.source_record_id for record in records)
        restore_job = session.get_one(Job, job_id)
        assert restore_job.result is not None
        preventive_id = restore_job.result["preventive_backup_record_id"]
        assert any(record.id == preventive_id for record in records)
    assert len(list(backups_directory.glob("palworld-manager-backup-*.tar.gz"))) == 3
    assert external.read_bytes() == b"preservar"


def test_interrupted_restore_is_not_resumed_and_requires_manual_review(
    restore_context: RestoreContext,
) -> None:
    factory = create_session_factory(restore_context.engine)
    job_id = _enqueue_restore(restore_context)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        job.status = "RUNNING"
        job.step = "RESTORING"
        job.result = {**(job.result or {}), "destructive_started": True}
    with session_scope(factory) as session:
        interrupted = recover_interrupted_jobs(session)

    assert [item.id for item in interrupted] == [job_id]
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "INTERRUPTED"
        assert job.result is not None
        assert job.result["requires_manual_review"] is True
    assert restore_context.worker.process_next() is False


def test_restore_web_requires_auth_csrf_exact_confirmation_and_only_enqueues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "fake-login-password")
        source_job = Job(kind="LOCAL_BACKUP", status="SUCCEEDED", progress=100)
        session.add(source_job)
        session.flush()
        record = BackupRecord(
            job_id=source_job.id,
            filename=(
                "palworld-manager-backup-20260814T120000000000Z-"
                f"j{source_job.id:06d}-{'a' * 32}.tar.gz"
            ),
            location="LOCAL",
            status="VALID",
            sha256="b" * 64,
            size_bytes=1024,
            storage_path="backups/managed.tar.gz",
        )
        session.add(record)
        session.flush()
        record_id = record.id
    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        login_csrf = client.get("/login").cookies.get(LOGIN_CSRF_COOKIE_NAME)
        assert login_csrf is not None
        assert (
            client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "fake-login-password",
                    "csrf_token": login_csrf,
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
        assert csrf is not None
        page = client.get("/backups")
        assert "Digite <strong>RESTAURAR</strong>" in page.text
        assert "manager.db" not in page.text
        assert 'data-confirm-tone="danger"' in page.text
        assert (
            client.post(
                f"/backups/{record_id}/restore",
                data={"confirmation": "RESTAURAR", "csrf_token": "invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/backups/{record_id}/restore",
                data={"confirmation": "restaurar", "csrf_token": csrf},
            ).status_code
            == 400
        )
        accepted = client.post(
            f"/backups/{record_id}/restore",
            data={"confirmation": "RESTAURAR", "csrf_token": csrf},
        )
        assert accepted.status_code == 202
        assert 'hx-trigger="every 1s"' in accepted.text
        assert "não pode ser cancelado" in accepted.text
        assert client.post("/backups/restore/jobs/1/cancel").status_code in {404, 405}
    unauthenticated = TestClient(create_app(settings), base_url="http://testserver")
    try:
        assert (
            unauthenticated.post(
                f"/backups/{record_id}/restore",
                data={"confirmation": "RESTAURAR"},
            ).status_code
            == 401
        )
    finally:
        unauthenticated.close()
        engine.dispose()
