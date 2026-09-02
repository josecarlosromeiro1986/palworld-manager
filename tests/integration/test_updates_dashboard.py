from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.main import create_app


@pytest.fixture
def updates_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Engine]]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    with session_scope(create_session_factory(engine)) as session:
        create_administrator(session, "admin", "fake-login-password")
    app = create_app(Settings(environment=AppEnvironment.TEST, manager_database=database_path))
    with TestClient(app, base_url="http://testserver") as client:
        yield client, engine
    engine.dispose()


def _login(client: TestClient) -> str:
    client.get("/login")
    login_csrf = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert login_csrf
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "fake-login-password",
            "csrf_token": login_csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf
    return csrf


def test_updates_page_requires_authentication_and_exposes_no_structural_paths(
    updates_client: tuple[TestClient, Engine],
) -> None:
    client, _engine = updates_client
    assert client.get("/updates", follow_redirects=False).status_code == 303
    _login(client)

    page = client.get("/updates")

    assert page.status_code == 200
    assert "Verificar atualizações" in page.text
    assert "2394010" in page.text
    assert "PALWORLD_DIR" not in page.text
    assert "/home/steam" not in page.text


def test_update_actions_require_csrf_and_exact_confirmation(
    updates_client: tuple[TestClient, Engine],
) -> None:
    client, _engine = updates_client
    csrf = _login(client)

    assert client.post("/updates/check", data={"csrf_token": "invalid"}).status_code == 403
    accepted = client.post("/updates/check", data={"csrf_token": csrf})
    assert accepted.status_code == 202
    assert 'data-job-status="PENDING"' in accepted.text

    invalid_update = client.post(
        "/updates",
        data={"confirmation": "atualizar", "csrf_token": csrf},
    )
    assert invalid_update.status_code == 400
    assert "Digite ATUALIZAR para confirmar." in invalid_update.text


def test_update_fragment_explains_safe_cancellation_boundary(
    updates_client: tuple[TestClient, Engine],
) -> None:
    client, _engine = updates_client
    csrf = _login(client)
    accepted = client.post("/updates/check", data={"csrf_token": csrf})

    assert 'hx-get="/updates/jobs/1"' in accepted.text
    assert "every 1s" in accepted.text
    assert "shell" not in accepted.text.lower()
