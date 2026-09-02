from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.history import AuditHistoryService
from app.audit.service import record_audit_event
from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting, AuditEvent, Job
from app.main import create_app

FIXED_NOW = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)
FAKE_REST_MARKER = "audit-rest-marker-not-a-credential"
FAKE_WEBHOOK_MARKER = "audit-webhook-marker-not-a-credential"
FAKE_WEBHOOK_URL = f"https://discord.com/api/webhooks/123/{FAKE_WEBHOOK_MARKER}"


@dataclass(frozen=True, slots=True)
class AuditContext:
    client: TestClient
    engine: Engine
    factory: sessionmaker[Session]
    user_id: int


@pytest.fixture
def audit_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[AuditContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        administrator = create_administrator(session, "admin", "senha-ficticia")
        session.flush()
        user_id = administrator.id
        session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
        for index in range(54):
            record_audit_event(
                session,
                occurred_at=FIXED_NOW - timedelta(minutes=index),
                action="UNBAN" if index == 0 else "BACKUP" if index % 2 == 0 else "LOGIN",
                result="FAILURE" if index == 0 else "SUCCESS",
                origin="ADMINISTRATOR" if index == 0 else "SYSTEM",
                user_id=user_id if index == 0 else None,
                target="Jogador exclusivo" if index == 0 else f"Alvo controlado {index}",
                reason="Motivo controlado" if index == 0 else None,
                details={"sequence": index},
            )
        session.add(
            AuditEvent(
                occurred_at=FIXED_NOW + timedelta(seconds=1),
                action="LEGACY_EVENT",
                result="SUCCESS",
                origin="SYSTEM",
                target=f"Alvo {FAKE_REST_MARKER}",
                reason=FAKE_WEBHOOK_URL,
                details={"legacy": FAKE_REST_MARKER, "token": FAKE_WEBHOOK_URL},
            )
        )
        session.add(
            AuditEvent(
                occurred_at=FIXED_NOW - timedelta(days=91),
                action="EXPIRED_EVENT",
                result="SUCCESS",
                origin="SYSTEM",
                target="Evento expirado",
            )
        )

    settings = Settings(
        environment=AppEnvironment.TEST,
        manager_database=database_path,
        palworld_rest_password=SecretStr(FAKE_REST_MARKER),
        discord_webhook_url=SecretStr(FAKE_WEBHOOK_URL),
    )
    application = create_app(settings)
    application.state.audit_history_service = AuditHistoryService(
        settings,
        factory,
        clock=lambda: FIXED_NOW,
    )
    with TestClient(application, base_url="http://testserver") as client:
        yield AuditContext(client, engine, factory, user_id)
    engine.dispose()


def _login(client: TestClient) -> None:
    response = client.get("/login")
    csrf = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert response.status_code == 200
    assert csrf is not None
    authenticated = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert authenticated.status_code == 303


def test_audit_route_requires_authentication(audit_context: AuditContext) -> None:
    response = audit_context.client.get("/audit", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_audit_history_paginates_prunes_localizes_and_redacts(
    audit_context: AuditContext,
) -> None:
    _login(audit_context.client)

    first = audit_context.client.get("/audit")
    second = audit_context.client.get("/audit?page=2")

    assert first.status_code == 200
    assert second.status_code == 200
    assert 'href="/audit"' in first.text
    assert 'aria-current="page"' in first.text
    assert first.text.count("data-audit-event=") == 50
    assert second.text.count("data-audit-event=") == 6
    assert "56 evento(s)" in first.text
    assert "21/08/2026 12:00:01" in first.text
    assert 'rel="next"' in first.text
    assert 'rel="prev"' in second.text
    assert "CSV" not in first.text
    assert FAKE_REST_MARKER not in first.text
    assert FAKE_WEBHOOK_MARKER not in first.text
    assert "SEGREDO PROTEGIDO" in first.text
    assert "Evento expirado" not in first.text
    with session_scope(audit_context.factory) as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == 56
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "EXPIRED_EVENT")
            )
            == 0
        )


def test_audit_history_combines_all_filters(audit_context: AuditContext) -> None:
    _login(audit_context.client)

    response = audit_context.client.get(
        "/audit",
        params={
            "date_from": "2026-08-21",
            "date_to": "2026-08-21",
            "action": "UNBAN",
            "result": "FAILURE",
            "origin": "ADMINISTRATOR",
            "user_id": str(audit_context.user_id),
            "target": "exclusivo",
        },
    )

    assert response.status_code == 200
    assert response.text.count("data-audit-event=") == 1
    assert 'data-audit-action="UNBAN"' in response.text
    assert 'data-audit-result="FAILURE"' in response.text
    assert 'data-audit-origin="ADMINISTRATOR"' in response.text
    assert "Jogador exclusivo" in response.text
    assert "Motivo controlado" in response.text


def test_audit_history_rejects_invalid_filters(audit_context: AuditContext) -> None:
    _login(audit_context.client)

    response = audit_context.client.get("/audit?date_from=21/08/2026")

    assert response.status_code == 400
    assert "Data inicial deve usar uma data válida." in response.text


def test_recorded_audit_derives_job_duration_and_redacts_sensitive_fields(
    audit_context: AuditContext,
) -> None:
    started_at = FIXED_NOW - timedelta(seconds=2, milliseconds=500)
    with session_scope(audit_context.factory) as session:
        job = Job(
            kind="AUDIT_TEST",
            status="SUCCEEDED",
            started_at=started_at,
            finished_at=FIXED_NOW,
        )
        session.add(job)
        session.flush()
        event = record_audit_event(
            session,
            occurred_at=FIXED_NOW,
            action="AUDIT_TEST",
            result="SUCCESS",
            origin="SYSTEM",
            job_id=job.id,
            target="password=valor-interno",
            details={"token": "valor-interno", "safe": "password=valor-interno"},
        )
        session.flush()
        event_id = event.id

    with session_scope(audit_context.factory) as session:
        event = session.get_one(AuditEvent, event_id)
        assert event.duration_ms == 2_500
        assert "valor-interno" not in str(event.target)
        assert "valor-interno" not in str(event.details)
