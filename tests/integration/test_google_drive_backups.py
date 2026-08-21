import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.backups.drive_jobs import (
    DRIVE_DOWNLOAD_JOB_KIND,
    DRIVE_UPLOAD_JOB_KIND,
    DriveJobConflictError,
    DriveJobExecutor,
    enqueue_drive_check,
    enqueue_drive_delete,
    enqueue_drive_download,
    enqueue_drive_upload,
    request_drive_cancel,
)
from app.backups.drive_service import DriveTransferService
from app.backups.jobs import LocalBackupJobExecutor, enqueue_local_backup
from app.backups.service import LocalBackupService
from app.backups.source import FakeBackupPayloadSource
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, AuditEvent, BackupRecord, Job, NotificationEvent
from app.integrations.google_drive import FakeGoogleDriveStorage
from app.integrations.palworld_rest import FakePalworldRestClient
from app.jobs.logs import MemoryJobLogStore
from app.jobs.service import claim_next_job, recover_interrupted_jobs
from app.lifecycle.service import FakeLifecycleEnvironment, PalworldLifecycleExecutor
from app.lifecycle.worker import LifecycleJobWorker
from app.main import create_app


@dataclass
class DriveContext:
    engine: Engine
    database_path: Path
    local: LocalBackupService
    drive: FakeGoogleDriveStorage
    worker: LifecycleJobWorker
    logs: MemoryJobLogStore


@pytest.fixture
def drive_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DriveContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")
    rest = FakePalworldRestClient()
    local = LocalBackupService(
        manager_database=database_path,
        session_factory=factory,
        payload_source=FakeBackupPayloadSource(rest),
    )
    drive = FakeGoogleDriveStorage()
    transfer = DriveTransferService(
        manager_database=database_path,
        local_backups=local,
        storage=drive,
    )
    logs = MemoryJobLogStore()
    lifecycle = FakeLifecycleEnvironment()
    worker = LifecycleJobWorker(
        factory,
        PalworldLifecycleExecutor(lifecycle, lifecycle, lifecycle),
        worker_id="drive-worker",
        backup_executor=LocalBackupJobExecutor(
            factory,
            local,
            automatic_drive_uploads=True,
        ),
        drive_executor=DriveJobExecutor(factory, transfer),
        job_logs=logs,
    )
    yield DriveContext(engine, database_path, local, drive, worker, logs)
    engine.dispose()


def _create_local(context: DriveContext, trigger: str = "MANUAL") -> BackupRecord:
    factory = create_session_factory(context.engine)
    with session_scope(factory) as session:
        job = enqueue_local_backup(
            session,
            user_id=1 if trigger == "MANUAL" else None,
            trigger=trigger,
        )
        job_id = job.id
    assert context.worker.process_next()
    with session_scope(factory) as session:
        record = session.scalar(
            select(BackupRecord).where(
                BackupRecord.job_id == job_id,
                BackupRecord.location == "LOCAL",
            )
        )
        assert record is not None
        session.expunge(record)
        return record


def _managed_filename(index: int) -> str:
    return (
        f"palworld-manager-backup-20260814T{120000 + index:06d}000000Z"
        f"-j{index + 100:06d}-{index:032x}.tar.gz"
    )


def test_valid_daily_backup_enqueues_and_completes_independent_upload(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context, "AUTOMATIC")
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        upload = session.scalar(select(Job).where(Job.kind == DRIVE_UPLOAD_JOB_KIND))
        assert upload is not None and upload.status == "PENDING"
        local_job = session.get_one(Job, local.job_id)
        assert local_job.status == "SUCCEEDED"
        assert (local_job.result or {}).get("drive_upload_job_id") == upload.id

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        local_record = session.get_one(BackupRecord, local.id)
        remote = session.scalar(select(BackupRecord).where(BackupRecord.location == "DRIVE"))
        assert remote is not None
        assert remote.filename == local_record.filename
        assert remote.sha256 == local_record.sha256
        assert remote.size_bytes == local_record.size_bytes
        assert remote.storage_path == remote.filename
        assert session.get_one(Job, upload.id).status == "SUCCEEDED"
    assert drive_context.drive.contains(local.filename)


def test_manual_backup_stays_local_until_upload_is_requested(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context)
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        assert session.scalar(select(Job).where(Job.kind == DRIVE_UPLOAD_JOB_KIND)) is None
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )
        assert upload.requires_maintenance_lock is True
        with pytest.raises(DriveJobConflictError):
            enqueue_drive_upload(
                session,
                backup_record_id=local.id,
                user_id=1,
                trigger="MANUAL",
            )

    assert drive_context.worker.process_next()
    with session_scope(factory) as session:
        assert session.get_one(Job, upload.id).status == "SUCCEEDED"


def test_drive_check_persists_status_and_quota(drive_context: DriveContext) -> None:
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        check = enqueue_drive_check(session, user_id=1)
        assert check.requires_maintenance_lock is False

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        stored = session.get_one(Job, check.id)
        assert stored.status == "SUCCEEDED"
        assert stored.result is not None
        assert stored.result["quota_total"] == 10 * 1024**3
        assert stored.result["remote_count"] == 0


def test_upload_failure_preserves_valid_local_and_creates_safe_notification(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context)
    local_path = drive_context.database_path.parent / local.storage_path
    drive_context.drive.set_failure("detalhe-interno-nao-exibir")
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        stored_upload = session.get_one(Job, upload.id)
        assert stored_upload.status == "FAILED"
        assert session.get_one(BackupRecord, local.id).status == "VALID"
        assert session.scalar(select(BackupRecord).where(BackupRecord.location == "DRIVE")) is None
        notification = session.scalar(
            select(NotificationEvent).where(NotificationEvent.job_id == upload.id)
        )
        audits = tuple(session.scalars(select(AuditEvent).where(AuditEvent.job_id == upload.id)))
        assert notification is not None and notification.event_type == "DRIVE_FAILED"
        assert "detalhe-interno-nao-exibir" not in json.dumps([event.details for event in audits])
        log_path = stored_upload.log_path
        assert log_path is not None
    assert local_path.exists()
    assert all(
        "detalhe-interno-nao-exibir" not in line for line in drive_context.logs.tail(log_path)
    )


def test_failure_after_remote_transfer_removes_only_new_manager_artifact(
    drive_context: DriveContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = _create_local(drive_context)
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )
    from app.backups import drive_jobs

    original = drive_jobs._safe_audit_details
    attempts = 0

    def fail_once(result: dict[str, object]) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("falha parcial simulada")
        return original(result)

    monkeypatch.setattr(drive_jobs, "_safe_audit_details", fail_once)

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        assert session.get_one(Job, upload.id).status == "FAILED"
        assert session.scalar(select(BackupRecord).where(BackupRecord.location == "DRIVE")) is None
        assert session.get_one(BackupRecord, local.id).status == "VALID"
    assert not drive_context.drive.contains(local.filename)


def test_upload_cancelled_at_safe_checkpoint_keeps_local_only(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context)
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )
        assert request_drive_cancel(session, upload.id, user_id=1)

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        assert session.get_one(Job, upload.id).status == "CANCELLED"
        assert session.scalar(select(BackupRecord).where(BackupRecord.location == "DRIVE")) is None
        assert session.get_one(BackupRecord, local.id).status == "VALID"


def test_remote_download_validates_before_local_publish_and_delete_preserves_local(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context, "AUTOMATIC")
    assert drive_context.worker.process_next()
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        remote = session.scalar(select(BackupRecord).where(BackupRecord.location == "DRIVE"))
        assert remote is not None
        remote_id = remote.id
        local_record = session.get_one(BackupRecord, local.id)
        assert drive_context.local.remove_managed_artifact(local_record.storage_path)
        session.delete(local_record)
        session.flush()
        download = enqueue_drive_download(session, backup_record_id=remote_id, user_id=1)

    assert drive_context.worker.process_next()
    with session_scope(factory) as session:
        assert session.get_one(Job, download.id).kind == DRIVE_DOWNLOAD_JOB_KIND
        downloaded = session.scalar(select(BackupRecord).where(BackupRecord.location == "LOCAL"))
        assert downloaded is not None and downloaded.sha256 == remote.sha256
        delete_job = enqueue_drive_delete(session, backup_record_id=remote_id, user_id=1)

    assert drive_context.worker.process_next()
    with session_scope(factory) as session:
        assert session.get(BackupRecord, remote_id) is None
        preserved_local = session.scalar(
            select(BackupRecord).where(BackupRecord.location == "LOCAL")
        )
        assert preserved_local is not None
        assert session.get_one(Job, delete_job.id).status == "SUCCEEDED"
    assert not drive_context.drive.contains(local.filename)


def test_corrupted_remote_download_creates_no_local_record_or_artifact(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context, "AUTOMATIC")
    assert drive_context.worker.process_next()
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        remote = session.scalar(select(BackupRecord).where(BackupRecord.location == "DRIVE"))
        assert remote is not None
        remote_id = remote.id
        local_record = session.get_one(BackupRecord, local.id)
        assert drive_context.local.remove_managed_artifact(local_record.storage_path)
        session.delete(local_record)
        session.flush()
        download = enqueue_drive_download(session, backup_record_id=remote_id, user_id=1)
    drive_context.drive.seed(local.filename, b"arquivo-corrompido")

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        assert session.get_one(Job, download.id).status == "FAILED"
        assert session.scalar(select(BackupRecord).where(BackupRecord.location == "LOCAL")) is None
    temporary_root = drive_context.database_path.parent / "tmp/drive"
    assert list(temporary_root.glob("job-*")) == []


def test_interrupted_upload_is_not_requeued_and_local_remains_valid(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context)
    factory = create_session_factory(drive_context.engine)
    with session_scope(factory) as session:
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )
    with session_scope(factory) as session:
        claimed = claim_next_job(session, "old-worker", (DRIVE_UPLOAD_JOB_KIND,))
        assert claimed is not None and claimed.id == upload.id
    with session_scope(factory) as session:
        interrupted = recover_interrupted_jobs(session)
        assert [item.id for item in interrupted] == [upload.id]
    with session_scope(factory) as session:
        assert claim_next_job(session, "new-worker", (DRIVE_UPLOAD_JOB_KIND,)) is None
        assert session.get_one(Job, upload.id).status == "INTERRUPTED"
        assert session.get_one(BackupRecord, local.id).status == "VALID"


def test_quota_removes_only_oldest_database_managed_remote(
    drive_context: DriveContext,
) -> None:
    local = _create_local(drive_context)
    factory = create_session_factory(drive_context.engine)
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    managed_names = (_managed_filename(1), _managed_filename(2))
    with session_scope(factory) as session:
        for index, filename in enumerate(managed_names):
            content = bytes([index + 1]) * 100
            drive_context.drive.seed(filename, content)
            session.add(
                BackupRecord(
                    job_id=None,
                    filename=filename,
                    location="DRIVE",
                    status="VALID",
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    storage_path=filename,
                    created_at=now + timedelta(seconds=index),
                )
            )
        drive_context.drive.seed_unmanaged("arquivo-do-usuario.txt", b"externo")
        upload = enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )
    drive_context.drive.set_total_bytes((local.size_bytes or 0) + 107)

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        names = set(
            session.scalars(select(BackupRecord.filename).where(BackupRecord.location == "DRIVE"))
        )
        assert session.get_one(Job, upload.id).status == "SUCCEEDED"
    assert managed_names[0] not in names
    assert managed_names[1] in names
    assert local.filename in names
    assert drive_context.drive.contains("arquivo-do-usuario.txt")


@pytest.mark.parametrize(
    ("configured_retention", "expected_retention"),
    [(None, 10), (3, 3)],
)
def test_remote_retention_uses_default_or_configured_value_and_preserves_unmanaged_file(
    drive_context: DriveContext,
    configured_retention: int | None,
    expected_retention: int,
) -> None:
    local = _create_local(drive_context)
    factory = create_session_factory(drive_context.engine)
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    oldest = _managed_filename(0)
    with session_scope(factory) as session:
        if configured_retention is not None:
            session.add(
                AppSetting(
                    key="drive_backup_retention",
                    value=configured_retention,
                )
            )
        for index in range(expected_retention):
            filename = _managed_filename(index)
            content = bytes([index])
            drive_context.drive.seed(filename, content)
            session.add(
                BackupRecord(
                    job_id=None,
                    filename=filename,
                    location="DRIVE",
                    status="VALID",
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=1,
                    storage_path=filename,
                    created_at=now + timedelta(seconds=index),
                )
            )
        drive_context.drive.seed_unmanaged("foto-da-familia.jpg", b"externo")
        enqueue_drive_upload(
            session,
            backup_record_id=local.id,
            user_id=1,
            trigger="MANUAL",
        )

    assert drive_context.worker.process_next()

    with session_scope(factory) as session:
        records = tuple(
            session.scalars(select(BackupRecord).where(BackupRecord.location == "DRIVE"))
        )
    assert len(records) == expected_retention
    assert not drive_context.drive.contains(oldest)
    assert drive_context.drive.contains("foto-da-familia.jpg")


def test_drive_web_actions_require_authentication_and_csrf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    filename = _managed_filename(42)
    download_filename = _managed_filename(43)
    delete_filename = _managed_filename(44)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")
        local = BackupRecord(
            job_id=None,
            filename=filename,
            location="LOCAL",
            status="VALID",
            sha256="a" * 64,
            size_bytes=1024,
            storage_path=f"backups/{filename}",
        )
        session.add(local)
        session.flush()
        local_id = local.id
        download_record = BackupRecord(
            job_id=None,
            filename=download_filename,
            location="DRIVE",
            status="VALID",
            sha256="b" * 64,
            size_bytes=2048,
            storage_path=download_filename,
        )
        delete_record = BackupRecord(
            job_id=None,
            filename=delete_filename,
            location="DRIVE",
            status="VALID",
            sha256="c" * 64,
            size_bytes=4096,
            storage_path=delete_filename,
        )
        session.add_all([download_record, delete_record])
        session.flush()
        download_id = download_record.id
        delete_id = delete_record.id
    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        login_csrf = client.get("/login").cookies.get(LOGIN_CSRF_COOKIE_NAME)
        assert login_csrf is not None
        assert (
            client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "senha-ficticia",
                    "csrf_token": login_csrf,
                },
                follow_redirects=False,
            ).status_code
            == 303
        )
        csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
        assert csrf is not None
        page = client.get("/backups")
        remote_list = client.get("/backups/drive/list")
        rejected = client.post(f"/backups/drive/upload/{local_id}", data={"csrf_token": "invalid"})
        rejected_check = client.post("/backups/drive/check", data={"csrf_token": "invalid"})
        rejected_download = client.post(
            f"/backups/drive/download/{download_id}", data={"csrf_token": "invalid"}
        )
        rejected_delete = client.post(
            f"/backups/drive/delete/{delete_id}", data={"csrf_token": "invalid"}
        )
        accepted = client.post(f"/backups/drive/upload/{local_id}", data={"csrf_token": csrf})
        accepted_check = client.post("/backups/drive/check", data={"csrf_token": csrf})
        accepted_download = client.post(
            f"/backups/drive/download/{download_id}", data={"csrf_token": csrf}
        )
        accepted_delete = client.post(
            f"/backups/drive/delete/{delete_id}", data={"csrf_token": csrf}
        )
        assert page.status_code == 200 and "Enviar ao Drive" in page.text
        assert remote_list.status_code == 200 and "Testar conexão e quota" in remote_list.text
        assert rejected.status_code == 403
        assert rejected_check.status_code == 403
        assert rejected_download.status_code == 403
        assert rejected_delete.status_code == 403
        assert accepted.status_code == 202
        assert accepted_check.status_code == 202
        assert accepted_download.status_code == 202
        assert accepted_delete.status_code == 202
        assert 'hx-trigger="every 1s"' in accepted.text
        assert "storage_path" not in page.text + remote_list.text

    with TestClient(create_app(settings), base_url="http://testserver") as unauthenticated:
        assert unauthenticated.post(f"/backups/drive/upload/{local_id}").status_code == 401
    engine.dispose()
