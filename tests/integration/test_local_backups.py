import json
import stat
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.backups.jobs import (
    LOCAL_BACKUP_JOB_KIND,
    BackupJobConflictError,
    LocalBackupJobExecutor,
    enqueue_local_backup,
    request_backup_cancel,
)
from app.backups.manifest import sha256_file, validate_archive
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
from app.integrations.palworld_rest import FakePalworldRestClient, PalworldRestErrorKind
from app.jobs.logs import MemoryJobLogStore
from app.jobs.service import GLOBAL_MAINTENANCE_LOCK, recover_interrupted_jobs
from app.lifecycle.service import FakeLifecycleEnvironment, PalworldLifecycleExecutor
from app.lifecycle.worker import LifecycleJobWorker
from app.main import create_app


@dataclass
class BackupContext:
    engine: Engine
    database_path: Path
    rest: FakePalworldRestClient
    service: LocalBackupService
    worker: LifecycleJobWorker
    logs: MemoryJobLogStore


@pytest.fixture
def backup_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[BackupContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")
    rest = FakePalworldRestClient()
    service = LocalBackupService(
        manager_database=database_path,
        session_factory=factory,
        payload_source=FakeBackupPayloadSource(rest),
    )
    logs = MemoryJobLogStore()
    fake_lifecycle = FakeLifecycleEnvironment()
    worker = LifecycleJobWorker(
        factory,
        PalworldLifecycleExecutor(fake_lifecycle, fake_lifecycle, fake_lifecycle),
        worker_id="backup-worker",
        backup_executor=LocalBackupJobExecutor(factory, service),
        job_logs=logs,
    )
    yield BackupContext(engine, database_path, rest, service, worker, logs)
    engine.dispose()


def _enqueue(context: BackupContext) -> int:
    factory = create_session_factory(context.engine)
    with session_scope(factory) as session:
        job = enqueue_local_backup(session, user_id=None, trigger="AUTOMATIC")
        return job.id


def test_valid_backup_contains_complete_payload_manifest_and_external_hash(
    backup_context: BackupContext,
) -> None:
    factory = create_session_factory(backup_context.engine)
    with session_scope(factory) as session:
        session.add_all(
            [
                AppSetting(key="development_note", value="not-exported"),
                AppSetting(key="timezone", value="America/Sao_Paulo"),
            ]
        )
    job_id = _enqueue(backup_context)

    assert backup_context.worker.process_next()

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        record = session.scalar(select(BackupRecord))
        audits = tuple(session.scalars(select(AuditEvent).where(AuditEvent.job_id == job_id)))
        assert record is not None
        assert job.status == "SUCCEEDED"
        assert job.progress == 100
        assert job.is_cancellable is False
        assert record.job_id == job_id
        assert record.status == "VALID"
        archive_path = backup_context.database_path.parent / record.storage_path
        assert record.sha256 == sha256_file(archive_path)
        assert record.size_bytes == archive_path.stat().st_size
        assert stat.S_IMODE(archive_path.stat().st_mode) == 0o640
        assert {event.action for event in audits} == {"BACKUP_REQUESTED", "BACKUP"}
        assert all("not-exported" not in json.dumps(event.details) for event in audits)

    manifest = validate_archive(archive_path)
    manifest_files = cast(list[dict[str, object]], manifest["files"])
    paths = {str(item["path"]) for item in manifest_files}
    assert {
        "world/00000000000000000000000000000000/Level.sav",
        "world/00000000000000000000000000000000/LevelMeta.sav",
        "world/00000000000000000000000000000000/Players/00000000000000000000000000000001.sav",
        "config/PalWorldSettings.ini",
        "manager/manager.db",
        "manager/settings.json",
    }.issubset(paths)
    assert "manifest.json" not in paths
    with tarfile.open(archive_path, "r:gz") as archive:
        settings_stream = archive.extractfile("manager/settings.json")
        assert settings_stream is not None
        manager_settings = settings_stream.read().decode()
    assert "America/Sao_Paulo" in manager_settings
    assert '"local_backup_retention":3' in manager_settings
    assert "development_note" not in manager_settings
    assert list(archive_path.parent.glob("*.sha256")) == []
    assert backup_context.rest.save_requests == 1
    assert any("Execução finalizada" in line for line in backup_context.logs.tail(job.log_path))


def test_safe_save_failure_creates_no_valid_record_or_artifact(
    backup_context: BackupContext,
) -> None:
    backup_context.rest.set_error(PalworldRestErrorKind.TIMEOUT)
    job_id = _enqueue(backup_context)

    assert backup_context.worker.process_next()

    factory = create_session_factory(backup_context.engine)
    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "FAILED"
        assert job.result == {"trigger": "AUTOMATIC", "error": "BACKUP_FAILED"}
        assert session.scalar(select(BackupRecord)) is None
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.job_id == job_id, AuditEvent.action == "BACKUP")
        )
        assert audit is not None and audit.result == "FAILURE"
        notification = session.scalar(
            select(NotificationEvent).where(NotificationEvent.job_id == job_id)
        )
        assert notification is not None
        assert notification.event_type == "BACKUP_FAILED"
    assert list((backup_context.database_path.parent / "backups").glob("*.tar.gz")) == []
    assert list((backup_context.database_path.parent / "tmp/backups").glob("job-*")) == []


def test_failure_after_publication_removes_only_new_managed_artifact(
    backup_context: BackupContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = backup_context.database_path.parent / "backups/arquivo-do-usuario.tar.gz"
    external.parent.mkdir()
    external.write_bytes(b"preservar")

    def fail_retention(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("falha simulada sem detalhes sensíveis")

    monkeypatch.setattr("app.backups.jobs._apply_retention", fail_retention)
    job_id = _enqueue(backup_context)

    assert backup_context.worker.process_next()

    factory = create_session_factory(backup_context.engine)
    with session_scope(factory) as session:
        assert session.get_one(Job, job_id).status == "FAILED"
        assert session.scalar(select(BackupRecord)) is None
    assert list(external.parent.glob("palworld-manager-backup-*.tar.gz")) == []
    assert external.read_bytes() == b"preservar"


def test_backup_cancelled_before_safe_save_is_clean_and_releases_lock(
    backup_context: BackupContext,
) -> None:
    job_id = _enqueue(backup_context)
    factory = create_session_factory(backup_context.engine)
    with session_scope(factory) as session:
        assert request_backup_cancel(session, job_id, user_id=1)

    assert backup_context.worker.process_next()

    with session_scope(factory) as session:
        job = session.get_one(Job, job_id)
        assert job.status == "CANCELLED"
        assert session.get(MaintenanceLock, GLOBAL_MAINTENANCE_LOCK) is None
        assert session.scalar(select(BackupRecord)) is None
    assert backup_context.rest.save_requests == 0


def test_backup_requires_global_lock_and_duplicate_request_is_rejected(
    backup_context: BackupContext,
) -> None:
    factory = create_session_factory(backup_context.engine)
    with session_scope(factory) as session:
        first = enqueue_local_backup(session, user_id=None, trigger="AUTOMATIC")
        assert first.requires_maintenance_lock is True
        with pytest.raises(BackupJobConflictError, match="backup local"):
            enqueue_local_backup(session, user_id=None, trigger="AUTOMATIC")


def test_retention_keeps_exactly_three_managed_backups_and_preserves_external_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    rest = FakePalworldRestClient()
    times = iter(datetime(2026, 8, 14, hour, tzinfo=UTC) for hour in range(4))
    identifiers = iter(UUID(int=value) for value in range(1, 5))
    service = LocalBackupService(
        manager_database=database_path,
        session_factory=factory,
        payload_source=FakeBackupPayloadSource(rest),
        clock=lambda: next(times),
        identifier_factory=lambda: next(identifiers),
    )
    fake = FakeLifecycleEnvironment()
    worker = LifecycleJobWorker(
        factory,
        PalworldLifecycleExecutor(fake, fake, fake),
        worker_id="retention-worker",
        backup_executor=LocalBackupJobExecutor(factory, service),
    )
    backups_directory = database_path.parent / "backups"
    backups_directory.mkdir()
    external = backups_directory / "arquivo-do-usuario.tar.gz"
    external.write_bytes(b"preservar")

    for _ in range(4):
        with session_scope(factory) as session:
            enqueue_local_backup(session, user_id=None, trigger="AUTOMATIC")
        assert worker.process_next()

    with session_scope(factory) as session:
        records = tuple(session.scalars(select(BackupRecord).order_by(BackupRecord.created_at)))
    assert len(records) == 3
    assert len(list(backups_directory.glob("palworld-manager-backup-*.tar.gz"))) == 3
    assert external.read_bytes() == b"preservar"
    engine.dispose()


def test_interrupted_backup_is_not_resumed_and_only_owned_temporary_area_is_removed(
    backup_context: BackupContext,
) -> None:
    factory = create_session_factory(backup_context.engine)
    with session_scope(factory) as session:
        job = Job(
            kind=LOCAL_BACKUP_JOB_KIND,
            status="RUNNING",
            step="COMPRESSING",
            requires_maintenance_lock=True,
            coordination_key="LOCAL_BACKUP",
        )
        session.add(job)
        session.flush()
        job_id = job.id
    temporary = backup_context.database_path.parent / f"tmp/backups/job-{job_id:06d}-deadbeef"
    temporary.mkdir(parents=True)
    (temporary / "partial").write_bytes(b"partial")
    unrelated = backup_context.database_path.parent / "tmp/backups/user-data"
    unrelated.mkdir()
    published = (
        backup_context.database_path.parent
        / "backups"
        / f"palworld-manager-backup-20260814T120000000000Z-j{job_id:06d}-{'0' * 32}.tar.gz"
    )
    published.parent.mkdir()
    published.write_bytes(b"interrompido")
    external = published.parent / "arquivo-do-usuario.tar.gz"
    external.write_bytes(b"preservar")
    symlink_target = backup_context.database_path.parent / "outside-user-file.tar.gz"
    symlink_target.write_bytes(b"preservar-link")
    managed_symlink = (
        published.parent
        / f"palworld-manager-backup-20260814T120001000000Z-j{job_id:06d}-{'1' * 32}.tar.gz"
    )
    managed_symlink.symlink_to(symlink_target)

    with session_scope(factory) as session:
        interrupted = recover_interrupted_jobs(session)
    assert [item.id for item in interrupted] == [job_id]
    assert backup_context.service.cleanup_temporary_artifacts() == 1
    assert backup_context.service.cleanup_interrupted_artifacts((job_id,)) == 1
    assert not temporary.exists()
    assert not published.exists()
    assert unrelated.exists()
    assert external.exists()
    assert not backup_context.service.remove_managed_artifact(f"backups/{managed_symlink.name}")
    assert not backup_context.service.remove_managed_artifact("../outside-user-file.tar.gz")
    assert not backup_context.service.remove_managed_artifact(str(symlink_target))
    assert symlink_target.read_bytes() == b"preservar-link"
    with session_scope(factory) as session:
        assert session.get_one(Job, job_id).status == "INTERRUPTED"


@dataclass(frozen=True)
class BackupWebContext:
    client: TestClient
    engine: Engine


@pytest.fixture
def backup_web_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[BackupWebContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")
    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        login_csrf = client.get("/login").cookies.get(LOGIN_CSRF_COOKIE_NAME)
        assert login_csrf is not None
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "senha-ficticia",
                "csrf_token": login_csrf,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        yield BackupWebContext(client, engine)
    engine.dispose()


def test_backup_web_actions_require_authentication_and_csrf_and_enqueue_only(
    backup_web_context: BackupWebContext,
) -> None:
    client = backup_web_context.client
    page = client.get("/backups")
    csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf is not None

    rejected = client.post("/backups", data={"csrf_token": "invalid"})
    accepted = client.post("/backups", data={"csrf_token": csrf})

    assert page.status_code == 200
    assert "Backup agora" in page.text
    assert "storage_path" not in page.text
    assert rejected.status_code == 403
    assert accepted.status_code == 202
    assert 'hx-trigger="every 1s"' in accepted.text
    factory = create_session_factory(backup_web_context.engine)
    with session_scope(factory) as session:
        job = session.scalar(select(Job).where(Job.kind == LOCAL_BACKUP_JOB_KIND))
        assert job is not None and job.status == "PENDING"
        job_id = job.id

    invalid_cancel = client.post(
        f"/backups/jobs/{job_id}/cancel",
        data={"csrf_token": "invalid"},
    )
    valid_cancel = client.post(
        f"/backups/jobs/{job_id}/cancel",
        data={"csrf_token": csrf},
    )
    assert invalid_cancel.status_code == 403
    assert valid_cancel.status_code == 200

    unauthenticated = TestClient(
        create_app(
            Settings(
                environment=AppEnvironment.TEST,
                manager_database=Path(str(backup_web_context.engine.url.database)),
            )
        ),
        base_url="http://testserver",
    )
    try:
        assert unauthenticated.post("/backups").status_code == 401
    finally:
        unauthenticated.close()


def test_terminal_backup_refreshes_local_backup_list_without_page_reload(
    backup_web_context: BackupWebContext,
) -> None:
    client = backup_web_context.client
    csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf is not None
    accepted = client.post("/backups", data={"csrf_token": csrf})
    assert accepted.status_code == 202

    factory = create_session_factory(backup_web_context.engine)
    with session_scope(factory) as session:
        job = session.scalar(select(Job).where(Job.kind == LOCAL_BACKUP_JOB_KIND))
        assert job is not None
        job.status = "SUCCEEDED"
        job.step = "COMPLETED"
        job.progress = 100
        job.is_cancellable = False
        session.add(
            BackupRecord(
                job_id=job.id,
                filename="palworld-manager-backup-test.tar.gz",
                location="LOCAL",
                status="VALID",
                sha256="a" * 64,
                size_bytes=1024,
                storage_path="backups/palworld-manager-backup-test.tar.gz",
            )
        )
        job_id = job.id

    terminal = client.get(f"/backups/jobs/{job_id}")
    refreshed_list = client.get("/backups/list")

    assert terminal.status_code == 200
    assert terminal.headers["HX-Trigger"] == "localBackupFinished"
    assert 'hx-trigger="every 1s"' not in terminal.text
    assert refreshed_list.status_code == 200
    assert 'hx-trigger="localBackupFinished from:body"' in refreshed_list.text
    assert "palworld-manager-backup-test.tar.gz" in refreshed_list.text
    assert f"#{job_id} · SUCCEEDED" in refreshed_list.text
