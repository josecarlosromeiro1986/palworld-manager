from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.backups.drive_jobs import DRIVE_CHECK_JOB_KIND
from app.config import AppEnvironment, Settings
from app.dashboard.metrics import HostMetricsService, RawHostMetrics
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, Job, NotificationEvent
from app.diagnostics.probes import FakeEnvironmentDiagnosticsProbe
from app.diagnostics.service import DiagnosticsService
from app.main import create_app
from app.notifications.service import DISCORD_TEST, NOTIFICATION_CHANNEL_DISCORD
from app.updates.jobs import UPDATE_CHECK_JOB_KIND

FIXED_NOW = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)
REST_SECRET = "diagnostics-rest-secret"
DISCORD_SECRET = "diagnostics-discord-secret"


class StaticMetricsSource:
    def read(self) -> RawHostMetrics:
        return RawHostMetrics(
            cpu_percent=12.5,
            memory_percent=25.0,
            memory_used_bytes=4 * 1024**3,
            memory_total_bytes=16 * 1024**3,
            disk_percent=50.0,
            disk_used_bytes=100 * 1024**3,
            disk_total_bytes=200 * 1024**3,
            disk_free_bytes=100 * 1024**3,
            network_received_bytes=1000,
            network_sent_bytes=2000,
        )


@dataclass
class DiagnosticsContext:
    client: TestClient
    engine: Engine


@pytest.fixture
def diagnostics_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[DiagnosticsContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")
        session.add_all(
            [
                Job(
                    kind=UPDATE_CHECK_JOB_KIND,
                    status="SUCCEEDED",
                    result={
                        "installed_build_id": "10000001",
                        "available_build_id": "10000002",
                    },
                ),
                Job(
                    kind=DRIVE_CHECK_JOB_KIND,
                    status="SUCCEEDED",
                    result={"remote_count": 2},
                ),
                NotificationEvent(
                    event_type=DISCORD_TEST,
                    channel=NOTIFICATION_CHANNEL_DISCORD,
                    status="SENT",
                    attempts=1,
                ),
            ]
        )

    settings = Settings(
        environment=AppEnvironment.TEST,
        manager_database=database_path,
        palworld_rest_username=SecretStr("diagnostics-user"),
        palworld_rest_password=SecretStr(REST_SECRET),
        discord_webhook_url=SecretStr(f"https://discord.com/api/webhooks/123/{DISCORD_SECRET}"),
    )
    application = create_app(settings)
    application.state.diagnostics_service = DiagnosticsService(
        settings,
        application.state.session_factory,
        application.state.palworld_health_check,
        application.state.worker_health_check,
        HostMetricsService(source=StaticMetricsSource(), clock=lambda: FIXED_NOW),
        application.state.palworld_log_source,
        FakeEnvironmentDiagnosticsProbe(commit="7af506404a21"),
        clock=lambda: FIXED_NOW,
    )
    with TestClient(application, base_url="http://testserver") as client:
        yield DiagnosticsContext(client, engine)
    engine.dispose()


def _login(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    csrf = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert csrf is not None
    login = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert login.status_code == 303


def _persistent_counts(engine: Engine) -> tuple[int, int, int]:
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        return (
            session.scalar(select(func.count(AuditEvent.id))) or 0,
            session.scalar(select(func.count(Job.id))) or 0,
            session.scalar(select(func.count(NotificationEvent.id))) or 0,
        )


def test_diagnostics_routes_require_authentication(
    diagnostics_context: DiagnosticsContext,
) -> None:
    page = diagnostics_context.client.get("/diagnostics", follow_redirects=False)
    fragment = diagnostics_context.client.get(
        "/diagnostics/report",
        follow_redirects=False,
    )

    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    assert fragment.status_code == 303
    assert fragment.headers["location"] == "/login"


def test_diagnostics_page_is_copyable_read_only_and_contains_no_secrets(
    diagnostics_context: DiagnosticsContext,
) -> None:
    client = diagnostics_context.client
    _login(client)
    before = _persistent_counts(diagnostics_context.engine)

    page = client.get("/diagnostics")
    fragment = client.get("/diagnostics/report")

    assert page.status_code == 200
    assert fragment.status_code == 200
    assert 'href="/diagnostics"' in page.text
    assert 'aria-current="page"' in page.text
    assert "Testar novamente" in page.text
    assert "Copiar diagnóstico" in page.text
    assert 'hx-get="/diagnostics/report"' in page.text
    assert "data-diagnostics-copy-source" in page.text
    assert 'data-diagnostic-check="manager-build"' in page.text
    assert 'data-diagnostic-check="palworld-health"' in page.text
    assert 'data-diagnostic-check="worker-health"' in page.text
    assert 'data-diagnostic-check="database"' in page.text
    assert "migration 0007" in page.text
    assert "Versão 1.0.0; commit 7af506404a21." in page.text
    assert "build instalado 10000001" in page.text
    assert "2 objeto(s) gerenciado(s)" in page.text
    assert "último teste foi entregue pelo worker" in page.text
    assert "layouts/app.html" not in fragment.text
    assert "Palworld Manager — Diagnóstico" in fragment.text
    assert REST_SECRET not in page.text
    assert DISCORD_SECRET not in page.text
    assert "diagnostics-user" not in page.text
    assert str(diagnostics_context.engine.url.database) not in page.text
    assert _persistent_counts(diagnostics_context.engine) == before
