import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.auth.cookies import (
    LOGIN_CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_CSRF_COOKIE_NAME,
)
from app.auth.passwords import verify_password
from app.auth.service import create_administrator
from app.backups.drive_jobs import DRIVE_CHECK_JOB_KIND, DriveJobExecutor
from app.backups.drive_service import DriveTransferService
from app.backups.jobs import LocalBackupJobExecutor
from app.backups.scheduler import schedule_daily_backup
from app.backups.service import LocalBackupService
from app.backups.source import FakeBackupPayloadSource
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, AuditEvent, Job, NotificationEvent, SessionRecord, User
from app.integrations.discord import FakeDiscordWebhook
from app.integrations.google_drive import FakeGoogleDriveStorage
from app.integrations.palworld_rest import FakePalworldRestClient
from app.jobs.logs import MemoryJobLogStore
from app.lifecycle.jobs import lifecycle_timeout
from app.lifecycle.service import (
    FakeLifecycleEnvironment,
    LifecycleAction,
    PalworldLifecycleExecutor,
)
from app.lifecycle.worker import LifecycleJobWorker
from app.main import create_app
from app.manager_settings.service import (
    OPERATIONAL_SETTING_KEYS,
    configured_drive_retention,
    configured_local_retention,
)
from app.notifications.service import (
    DISCORD_TEST,
    NOTIFICATION_STATUS_PENDING,
    NOTIFICATION_STATUS_SENT,
    DiscordNotificationDispatcher,
)
from app.shutdown.jobs import assisted_shutdown_default


@dataclass
class ManagerSettingsContext:
    client: TestClient
    engine: Engine
    worker: LifecycleJobWorker


@pytest.fixture
def manager_settings_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ManagerSettingsContext]:
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
    transfer = DriveTransferService(
        manager_database=database_path,
        local_backups=local,
        storage=FakeGoogleDriveStorage(),
    )
    lifecycle = FakeLifecycleEnvironment()
    worker = LifecycleJobWorker(
        factory,
        PalworldLifecycleExecutor(lifecycle, lifecycle, lifecycle),
        worker_id="manager-settings-worker",
        backup_executor=LocalBackupJobExecutor(factory, local),
        drive_executor=DriveJobExecutor(factory, transfer),
        job_logs=MemoryJobLogStore(),
    )
    application = create_app(
        Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    )
    with TestClient(application, base_url="http://testserver") as client:
        yield ManagerSettingsContext(client, engine, worker)
    engine.dispose()


def _login(client: TestClient, password: str = "senha-ficticia") -> None:
    response = client.get("/login")
    assert response.status_code == 200
    csrf_token = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert csrf_token is not None
    login = client.post(
        "/login",
        data={"username": "admin", "password": password, "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert login.status_code == 303


def _csrf(client: TestClient) -> str:
    value = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert value is not None
    return value


def _settings_version(html: str) -> str:
    match = re.search(r'name=[\'"]settings_version[\'"] value=[\'"]([0-9a-f]{64})', html)
    assert match is not None
    return match.group(1)


def _operational_data(version: str, csrf_token: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "settings_version": version,
        "backup_enabled": "true",
        "backup_time": "03:15",
        "timezone": "America/Fortaleza",
        "local_backup_retention": "7",
        "drive_backup_retention": "25",
        "metrics_interval_seconds": "7",
        "assisted_shutdown_default_minutes": "10",
        "start_timeout_seconds": "240",
        "restart_timeout_seconds": "360",
        "stop_timeout_seconds": "120",
        "disk_warning_gb": "40",
        "disk_critical_gb": "15",
    }


def test_manager_settings_routes_require_authentication_and_hide_structural_fields(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    private = client.get("/manager-settings", follow_redirects=False)
    assert private.status_code == 303
    assert private.headers["location"] == "/login"

    _login(client)
    page = client.get("/manager-settings")

    assert page.status_code == 200
    assert "Configurações do Painel" in page.text
    assert "local_backup_retention" in page.text
    assert "current_password" in page.text
    assert "DISCORD_WEBHOOK_URL" not in page.text
    assert "PALWORLD_SERVICE" not in page.text
    assert "STEAMCMD" not in page.text
    assert "RCLONE_REMOTE" not in page.text
    assert "TAILSCALE" not in page.text
    assert "/home/steam" not in page.text
    backup_control = page.text.split("data-backup-enabled-control", maxsplit=1)[1].split(
        "</label>", maxsplit=1
    )[0]
    assert "rounded-md" not in backup_control
    assert "border" not in backup_control
    assert "bg-canvas" not in backup_control


def test_operational_update_requires_csrf_and_rejects_dangerous_fields(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    page = client.get("/manager-settings")
    version = _settings_version(page.text)
    data = _operational_data(version, _csrf(client))

    invalid_csrf = client.post(
        "/manager-settings/operational",
        data={**data, "csrf_token": "invalid"},
    )
    dangerous = client.post(
        "/manager-settings/operational",
        data={**data, "palworld_service": "other.service"},
    )

    assert invalid_csrf.status_code == 403
    assert dangerous.status_code == 400
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        assert tuple(session.scalars(select(AppSetting))) == ()
        failure = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "MANAGER_SETTINGS_UPDATE")
            .order_by(AuditEvent.id.desc())
        )
        assert failure is not None
        assert failure.result == "FAILURE"
        assert failure.details == {"error": "VALIDATION_FAILED"}


def test_stale_form_is_rejected_instead_of_overwriting_newer_values(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    page = client.get("/manager-settings")
    version = _settings_version(page.text)
    first = _operational_data(version, _csrf(client))
    second = {**first, "metrics_interval_seconds": "11"}

    assert client.post("/manager-settings/operational", data=first).status_code == 200
    stale = client.post("/manager-settings/operational", data=second)

    assert stale.status_code == 409
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        assert session.get_one(AppSetting, "metrics_interval_seconds").value == 7


def test_successful_operational_update_uses_prg_and_consumes_success_message(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    page = client.get("/manager-settings")
    data = _operational_data(_settings_version(page.text), _csrf(client))

    response = client.post(
        "/manager-settings/operational",
        data=data,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/manager-settings"
    assert "palworld_manager_operational_saved=saved" in response.headers["set-cookie"]

    landing = client.get(response.headers["location"])
    assert landing.status_code == 200
    assert "Configurações operacionais salvas." in landing.text

    refreshed = client.get("/manager-settings")
    assert "Configurações operacionais salvas." not in refreshed.text
    assert "As configurações foram alteradas em outra solicitação." not in refreshed.text


def test_password_change_requires_current_password_and_revokes_all_sessions(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    _login(client)
    csrf_token = _csrf(client)

    rejected = client.post(
        "/manager-settings/password",
        data={
            "csrf_token": "invalid",
            "current_password": "senha-ficticia",
            "new_password": "nova-senha-ficticia",
            "new_password_confirmation": "nova-senha-ficticia",
        },
    )
    assert rejected.status_code == 403

    response = client.post(
        "/manager-settings/password",
        data={
            "csrf_token": csrf_token,
            "current_password": "senha-ficticia",
            "new_password": "nova-senha-ficticia",
            "new_password_confirmation": "nova-senha-ficticia",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?password_changed=1"
    assert client.cookies.get(SESSION_COOKIE_NAME) is None
    assert client.cookies.get(SESSION_CSRF_COOKIE_NAME) is None
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        administrator = session.scalar(select(User).where(User.username == "admin"))
        assert administrator is not None
        assert verify_password("nova-senha-ficticia", administrator.password_hash)
        assert not verify_password("senha-ficticia", administrator.password_hash)
        sessions = tuple(session.scalars(select(SessionRecord)))
        assert len(sessions) == 2
        assert all(record.revoked_at is not None for record in sessions)
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "MANAGER_PASSWORD_UPDATE")
            .order_by(AuditEvent.id.desc())
        )
        assert audit is not None
        assert audit.result == "SUCCESS"
        assert audit.details is None
        assert audit.reason is None

    login_page = client.get("/login?password_changed=1")
    assert "Senha alterada. Entre novamente." in login_page.text
    _login(client, "nova-senha-ficticia")


@pytest.mark.parametrize(
    ("new_password", "confirmation", "reason"),
    [
        ("curta", "curta", "POLICY_REJECTED"),
        ("nova-senha-ficticia", "confirmacao-diferente", "CONFIRMATION_MISMATCH"),
    ],
)
def test_password_change_rejects_policy_or_confirmation_without_sensitive_audit(
    manager_settings_context: ManagerSettingsContext,
    new_password: str,
    confirmation: str,
    reason: str,
) -> None:
    client = manager_settings_context.client
    _login(client)

    response = client.post(
        "/manager-settings/password",
        data={
            "csrf_token": _csrf(client),
            "current_password": "senha-ficticia",
            "new_password": new_password,
            "new_password_confirmation": confirmation,
        },
    )

    assert response.status_code == 400
    assert client.cookies.get(SESSION_COOKIE_NAME) is not None
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        administrator = session.scalar(select(User).where(User.username == "admin"))
        assert administrator is not None
        assert verify_password("senha-ficticia", administrator.password_hash)
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "MANAGER_PASSWORD_UPDATE")
            .order_by(AuditEvent.id.desc())
        )
        assert audit is not None
        assert audit.result == "FAILURE"
        assert audit.reason == reason
        assert audit.details is None


def test_password_change_reuses_abuse_protection_for_current_password(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    data = {
        "csrf_token": _csrf(client),
        "current_password": "senha-atual-incorreta",
        "new_password": "nova-senha-ficticia",
        "new_password_confirmation": "nova-senha-ficticia",
    }

    responses = [client.post("/manager-settings/password", data=data) for _ in range(5)]

    assert [response.status_code for response in responses] == [400, 400, 400, 400, 429]
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        administrator = session.scalar(select(User).where(User.username == "admin"))
        assert administrator is not None
        assert verify_password("senha-ficticia", administrator.password_hash)
        event = session.scalar(
            select(NotificationEvent)
            .where(NotificationEvent.event_type == "LOGIN_BLOCKED")
            .order_by(NotificationEvent.id.desc())
        )
        assert event is not None


def test_discord_test_uses_notification_event_and_worker_fake(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    csrf_token = _csrf(client)
    assert (
        client.post("/manager-settings/discord-test", data={"csrf_token": "invalid"}).status_code
        == 403
    )

    first = client.post(
        "/manager-settings/discord-test",
        data={"csrf_token": csrf_token},
    )
    second = client.post(
        "/manager-settings/discord-test",
        data={"csrf_token": csrf_token},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        events = tuple(
            session.scalars(
                select(NotificationEvent).where(NotificationEvent.event_type == DISCORD_TEST)
            )
        )
        assert len(events) == 1
        event_id = events[0].id
        assert events[0].status == NOTIFICATION_STATUS_PENDING
    webhook = FakeDiscordWebhook()
    assert DiscordNotificationDispatcher(factory, webhook).process_next()
    assert len(webhook.messages) == 1
    assert "Teste de notificação" in webhook.messages[0].content

    status = client.get(f"/manager-settings/discord-tests/{event_id}")
    assert status.status_code == 200
    assert NOTIFICATION_STATUS_SENT in status.text


def test_drive_test_uses_persistent_job_and_worker_fake_without_double_submit(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    csrf_token = _csrf(client)
    assert (
        client.post("/manager-settings/drive-test", data={"csrf_token": "invalid"}).status_code
        == 403
    )

    first = client.post("/manager-settings/drive-test", data={"csrf_token": csrf_token})
    second = client.post("/manager-settings/drive-test", data={"csrf_token": csrf_token})

    assert first.status_code == 200
    assert second.status_code == 200
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        jobs = tuple(session.scalars(select(Job).where(Job.kind == DRIVE_CHECK_JOB_KIND)))
        assert len(jobs) == 1
        job_id = jobs[0].id
        assert jobs[0].status == "PENDING"
        assert jobs[0].requires_maintenance_lock is False

    assert manager_settings_context.worker.process_next()
    with session_scope(factory) as session:
        completed = session.get_one(Job, job_id)
        assert completed.status == "SUCCEEDED"
        assert completed.result is not None
        assert completed.result["quota_total"] == 10 * 1024**3
        assert completed.result["remote_count"] == 0

    status = client.get(f"/manager-settings/drive-tests/{job_id}")
    assert status.status_code == 200
    assert "SUCCEEDED" in status.text


def test_operational_values_persist_and_apply_to_existing_services(
    manager_settings_context: ManagerSettingsContext,
) -> None:
    client = manager_settings_context.client
    _login(client)
    page = client.get("/manager-settings")
    data = _operational_data(_settings_version(page.text), _csrf(client))
    data.pop("backup_enabled")

    response = client.post("/manager-settings/operational", data=data)

    assert response.status_code == 200
    assert "Configurações operacionais salvas." in response.text
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        stored = {
            item.key: item.value
            for item in session.scalars(
                select(AppSetting).where(AppSetting.key.in_(OPERATIONAL_SETTING_KEYS))
            )
        }
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "MANAGER_SETTINGS_UPDATE")
            .order_by(AuditEvent.id.desc())
        )
        assert stored["backup_enabled"] is False
        assert stored["timezone"] == "America/Fortaleza"
        assert stored["local_backup_retention"] == 7
        assert stored["drive_backup_retention"] == 25
        assert stored["metrics_interval_seconds"] == 7
        assert configured_local_retention(session) == 7
        assert configured_drive_retention(session) == 25
        assert assisted_shutdown_default(session) == 10
        assert lifecycle_timeout(session, LifecycleAction.START) == 240
        assert lifecycle_timeout(session, LifecycleAction.RESTART) == 360
        assert lifecycle_timeout(session, LifecycleAction.STOP) == 120
        assert schedule_daily_backup(session) is False
        assert audit is not None
        assert audit.result == "SUCCESS"
        assert set(audit.details or {}) == {"changed_keys"}
        assert (audit.details or {}).get("changed_keys") == sorted(OPERATIONAL_SETTING_KEYS)
        assert "America/Fortaleza" not in repr(audit.details)
        assert "03:15" not in repr(audit.details)

    dashboard = client.get("/")
    assert 'hx-trigger="load, every 7s"' in dashboard.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "Invalid/Timezone"),
        ("backup_time", "24:00"),
        ("local_backup_retention", "31"),
        ("metrics_interval_seconds", "0"),
        ("disk_critical_gb", "40"),
    ],
)
def test_backend_rejects_invalid_operational_values_without_partial_write(
    manager_settings_context: ManagerSettingsContext,
    field: str,
    value: str,
) -> None:
    client = manager_settings_context.client
    _login(client)
    page = client.get("/manager-settings")
    data = _operational_data(_settings_version(page.text), _csrf(client))
    data[field] = value

    response = client.post("/manager-settings/operational", data=data)

    assert response.status_code == 400
    factory = create_session_factory(manager_settings_context.engine)
    with session_scope(factory) as session:
        assert tuple(session.scalars(select(AppSetting))) == ()
