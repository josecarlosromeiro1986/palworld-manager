import hashlib
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
from app.backups.drive_jobs import DriveJobExecutor, enqueue_drive_upload
from app.backups.drive_service import DriveTransferService
from app.backups.jobs import LocalBackupJobExecutor, enqueue_local_backup
from app.backups.service import LocalBackupService
from app.backups.source import FakeBackupPayloadSource
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, AuditEvent, BackupRecord, Job, MaintenanceLock
from app.health.palworld import PalworldHealthState
from app.integrations.google_drive import FakeGoogleDriveStorage
from app.integrations.palworld_rest import FakePalworldRestClient
from app.jobs.logs import MemoryJobLogStore
from app.jobs.service import GLOBAL_MAINTENANCE_LOCK, claim_next_job, recover_interrupted_jobs
from app.lifecycle.service import (
    FakeLifecycleEnvironment,
    LifecycleAction,
    LifecycleOutcome,
    LifecycleResult,
)
from app.lifecycle.worker import LifecycleJobWorker
from app.logs.service import LogEntry
from app.main import create_app
from app.palworld_settings.storage import FakePalworldSettingsStorage
from app.restores.jobs import (
    REMOTE_RESTORE_JOB_KIND,
    LocalRestoreJobExecutor,
    RestoreJobConflictError,
    RestoreRequestError,
    enqueue_local_restore,
    enqueue_remote_restore,
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


class EmptyLogs:
    def history(self, limit: int) -> list[LogEntry]:
        assert limit == 100
        return []

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]:
        del after_cursor
        return iter(())


@dataclass
class RemoteRestoreContext:
    engine: Engine
    database_path: Path
    rest: FakePalworldRestClient
    local: LocalBackupService
    drive: FakeGoogleDriveStorage
    transfer: DriveTransferService
    target: FakeRestoreTarget
    lifecycle: RecordingLifecycle
    worker: LifecycleJobWorker
    local_record_id: int
    remote_record_id: int
    filename: str


@pytest.fixture
def remote_restore_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RemoteRestoreContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "fake-login-password")
        session.add(AppSetting(key="development_note", value="keep-manager-state"))
    rest = FakePalworldRestClient()
    backup_health = FakeLifecycleEnvironment()
    backup_health.start()
    local = LocalBackupService(
        manager_database=database_path,
        session_factory=factory,
        payload_source=FakeBackupPayloadSource(rest),
        palworld_health=backup_health,
    )
    drive = FakeGoogleDriveStorage()
    transfer = DriveTransferService(
        manager_database=database_path,
        local_backups=local,
        storage=drive,
    )
    target = FakeRestoreTarget(
        FakePalworldSettingsStorage(
            "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=("
            'ServerName="Atual",AdminPassword="fake-current-admin",'
            'ServerPassword="fake-current-server",FutureSetting=(Mode="Keep,Me"))\n'
        )
    )
    lifecycle = RecordingLifecycle()
    restore_service = LocalRestoreService(
        manager_database=database_path,
        backup_service=local,
        target=target,
    )
    worker = LifecycleJobWorker(
        factory,
        lifecycle,
        worker_id="remote-restore-worker",
        backup_executor=LocalBackupJobExecutor(factory, local),
        drive_executor=DriveJobExecutor(factory, transfer),
        restore_executor=LocalRestoreJobExecutor(
            factory,
            restore_service,
            local,
            lifecycle,
            EmptyLogs(),
            transfer,
        ),
        job_logs=MemoryJobLogStore(),
    )
    with session_scope(factory) as session:
        backup_job = enqueue_local_backup(session, user_id=1, trigger="MANUAL")
        backup_job_id = backup_job.id
    assert worker.process_next()
    with session_scope(factory) as session:
        local_record = session.scalar(
            select(BackupRecord).where(
                BackupRecord.job_id == backup_job_id,
                BackupRecord.location == "LOCAL",
            )
        )
        assert local_record is not None
        local_record_id = local_record.id
        filename = local_record.filename
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local_record.id,
            user_id=1,
            trigger="MANUAL",
        )
        upload_id = upload.id
    assert worker.process_next()
    with session_scope(factory) as session:
        remote_record = session.scalar(
            select(BackupRecord).where(
                BackupRecord.job_id == upload_id,
                BackupRecord.location == "DRIVE",
            )
        )
        assert remote_record is not None
        remote_record_id = remote_record.id
    yield RemoteRestoreContext(
        engine,
        database_path,
        rest,
        local,
        drive,
        transfer,
        target,
        lifecycle,
        worker,
        local_record_id,
        remote_record_id,
        filename,
    )
    engine.dispose()


def _enqueue_remote(context: RemoteRestoreContext) -> int:
    factory = create_session_factory(context.engine)
    with session_scope(factory) as session:
        job = enqueue_remote_restore(
            session,
            backup_record_id=context.remote_record_id,
            confirmation="RESTAURAR",
            user_id=1,
        )
        return job.id


def _remove_local_copy(context: RemoteRestoreContext) -> None:
    factory = create_session_factory(context.engine)
    with session_scope(factory) as session:
        record = session.get_one(BackupRecord, context.local_record_id)
        context.local.remove_managed_artifact(record.storage_path)
        session.delete(record)


def test_remote_restore_downloads_temporarily_and_reuses_full_safe_flow(
    remote_restore_context: RemoteRestoreContext,
) -> None:
    _remove_local_copy(remote_restore_context)
    factory = create_session_factory(remote_restore_context.engine)
    job_id = _enqueue_remote(remote_restore_context)

    with session_scope(factory) as session:
        pending = session.get_one(Job, job_id)
        assert pending.kind == REMOTE_RESTORE_JOB_KIND
        assert pending.requires_maintenance_lock is True
        assert pending.is_cancellable is False

    assert remote_restore_context.worker.process_next()

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "SUCCEEDED"
        assert job.result is not None
        assert job.result["source_location"] == "DRIVE"
        assert job.result["final_state"] == "ONLINE"
        assert isinstance(job.result["preventive_backup_record_id"], int)
        manager_setting = session.get(AppSetting, "development_note")
        assert manager_setting is not None
        assert manager_setting.value == "keep-manager-state"
        assert (
            session.scalar(
                select(func.count())
                .select_from(BackupRecord)
                .where(
                    BackupRecord.location == "LOCAL",
                    BackupRecord.filename == remote_restore_context.filename,
                )
            )
            == 0
        )
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.job_id == job_id, AuditEvent.action == "RESTORE")
            .order_by(AuditEvent.id.desc())
        )
        assert audit is not None and audit.result == "SUCCESS"
        assert audit.target == "Backup remoto"
        assert audit.details is not None and audit.details["source_location"] == "DRIVE"
    assert remote_restore_context.lifecycle.actions == [
        LifecycleAction.STOP,
        LifecycleAction.START,
    ]
    assert remote_restore_context.target.apply_calls == 1
    assert any(path.endswith("Level.sav") for path in remote_restore_context.target.world_files)
    current_ini = remote_restore_context.target.storage.read().content
    assert 'AdminPassword="fake-current-admin"' in current_ini
    assert 'ServerPassword="fake-current-server"' in current_ini
    assert list((remote_restore_context.database_path.parent / "tmp/drive").glob("job-*")) == []
    assert list((remote_restore_context.database_path.parent / "tmp/restores").glob("job-*")) == []


@pytest.mark.parametrize("invalid_internal_archive", [False, True])
def test_remote_restore_rejects_sha_or_archive_before_preventive_backup_and_stop(
    remote_restore_context: RemoteRestoreContext,
    invalid_internal_archive: bool,
) -> None:
    _remove_local_copy(remote_restore_context)
    corrupt = b"not-a-valid-managed-tar-gz"
    remote_restore_context.drive.seed(remote_restore_context.filename, corrupt)
    if invalid_internal_archive:
        factory = create_session_factory(remote_restore_context.engine)
        with session_scope(factory) as session:
            record = session.get_one(BackupRecord, remote_restore_context.remote_record_id)
            record.sha256 = hashlib.sha256(corrupt).hexdigest()
            record.size_bytes = len(corrupt)
    save_requests_before = remote_restore_context.rest.save_requests
    job_id = _enqueue_remote(remote_restore_context)

    assert remote_restore_context.worker.process_next()

    factory = create_session_factory(remote_restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "REMOTE_DOWNLOAD_INVALID"
        assert job.result["requires_manual_review"] is False
        assert (
            session.get_one(BackupRecord, remote_restore_context.remote_record_id).status == "VALID"
        )
    assert remote_restore_context.rest.save_requests == save_requests_before
    assert remote_restore_context.lifecycle.actions == []
    assert remote_restore_context.target.apply_calls == 0
    assert list((remote_restore_context.database_path.parent / "tmp/drive").glob("job-*")) == []


def test_remote_restore_failure_after_apply_requires_manual_review_and_keeps_remote(
    remote_restore_context: RemoteRestoreContext,
) -> None:
    _remove_local_copy(remote_restore_context)
    remote_restore_context.lifecycle.fail_action = LifecycleAction.START
    job_id = _enqueue_remote(remote_restore_context)

    assert remote_restore_context.worker.process_next()

    factory = create_session_factory(remote_restore_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["error"] == "START_FAILED"
        assert job.result["requires_manual_review"] is True
        assert (
            session.get_one(BackupRecord, remote_restore_context.remote_record_id).status == "VALID"
        )
        assert isinstance(job.result["preventive_backup_record_id"], int)
    assert remote_restore_context.target.apply_calls == 1
    assert remote_restore_context.drive.contains(remote_restore_context.filename)


def test_remote_and_local_restore_share_duplicate_guard_and_interruption_is_not_resumed(
    remote_restore_context: RemoteRestoreContext,
) -> None:
    factory = create_session_factory(remote_restore_context.engine)
    with session_scope(factory) as session:
        with pytest.raises(RestoreRequestError):
            enqueue_remote_restore(
                session,
                backup_record_id=remote_restore_context.remote_record_id,
                confirmation="restaurar",
                user_id=1,
            )
        remote = enqueue_remote_restore(
            session,
            backup_record_id=remote_restore_context.remote_record_id,
            confirmation="RESTAURAR",
            user_id=1,
        )
        with pytest.raises(RestoreJobConflictError):
            enqueue_local_restore(
                session,
                backup_record_id=remote_restore_context.local_record_id,
                confirmation="RESTAURAR",
                user_id=1,
            )
        claimed = claim_next_job(
            session,
            "interrupted-worker",
            (REMOTE_RESTORE_JOB_KIND,),
        )
        assert claimed is not None and claimed.id == remote.id
        assert session.get(MaintenanceLock, GLOBAL_MAINTENANCE_LOCK) is not None
        interrupted = recover_interrupted_jobs(session)
        assert [item.id for item in interrupted] == [remote.id]
        remote_id = remote.id

    assert remote_restore_context.worker.process_next() is False
    with session_scope(factory) as session:
        job = session.get_one(Job, remote_id)
        assert job.status == "INTERRUPTED"
        assert job.result is not None and job.result["requires_manual_review"] is True
    assert remote_restore_context.lifecycle.actions == []


def test_remote_restore_web_requires_auth_csrf_and_exact_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    filename = f"palworld-manager-backup-20260814T120000000000Z-j000123-{'d' * 32}.tar.gz"
    with session_scope(factory) as session:
        create_administrator(session, "admin", "fake-login-password")
        remote = BackupRecord(
            job_id=None,
            filename=filename,
            location="DRIVE",
            status="VALID",
            sha256="e" * 64,
            size_bytes=2048,
            storage_path=filename,
            created_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(remote)
        session.flush()
        remote_id = remote.id
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
        remote_list = client.get("/backups/drive/list")
        assert "Restore remoto" in remote_list.text
        assert "RESTAURAR" in remote_list.text
        assert (
            client.post(
                f"/backups/drive/{remote_id}/restore",
                data={"confirmation": "RESTAURAR", "csrf_token": "invalid"},
            ).status_code
            == 403
        )
        invalid = client.post(
            f"/backups/drive/{remote_id}/restore",
            data={"confirmation": "restaurar", "csrf_token": csrf},
        )
        assert invalid.status_code == 400
        assert "Digite RESTAURAR exatamente para confirmar." in invalid.text
        accepted = client.post(
            f"/backups/drive/{remote_id}/restore",
            data={"confirmation": "RESTAURAR", "csrf_token": csrf},
        )
        assert accepted.status_code == 202
        assert "Restore remoto" in accepted.text
        assert 'hx-trigger="every 1s"' in accepted.text
        with session_scope(factory) as session:
            job = session.scalar(select(Job).where(Job.kind == REMOTE_RESTORE_JOB_KIND))
            assert job is not None and job.status == "PENDING"
            job_id = job.id
        assert client.get(f"/backups/restore/jobs/{job_id}").status_code == 200

    with TestClient(create_app(settings), base_url="http://testserver") as unauthenticated:
        assert (
            unauthenticated.post(
                f"/backups/drive/{remote_id}/restore",
                data={"confirmation": "RESTAURAR"},
            ).status_code
            == 401
        )
    engine.dispose()
