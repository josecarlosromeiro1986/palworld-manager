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
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, LoginAttempt, SessionRecord
from app.main import create_app


@dataclass(frozen=True)
class AuthenticationContext:
    client: TestClient
    engine: Engine


@pytest.fixture
def authentication_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[AuthenticationContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")

    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        yield AuthenticationContext(client=client, engine=engine)
    engine.dispose()


def _login_csrf(client: TestClient) -> str:
    response = client.get("/login")
    assert response.status_code == 200
    csrf_token = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert csrf_token is not None
    assert csrf_token in response.text
    return csrf_token


def _login(client: TestClient) -> None:
    csrf_token = _login_csrf(client)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_private_routes_require_session_but_health_and_login_are_public(
    authentication_context: AuthenticationContext,
) -> None:
    client = authentication_context.client

    private_response = client.get("/", follow_redirects=False)
    unsafe_response = client.post("/logout", follow_redirects=False)

    assert private_response.status_code == 303
    assert private_response.headers["location"] == "/login"
    assert unsafe_response.status_code == 401
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/login").status_code == 200


def test_login_requires_csrf_and_uses_generic_credentials_error(
    authentication_context: AuthenticationContext,
) -> None:
    client = authentication_context.client
    csrf_token = _login_csrf(client)

    csrf_failure = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": "invalido"},
    )
    credentials_failure = client.post(
        "/login",
        data={"username": "ausente", "password": "senha-incorreta", "csrf_token": csrf_token},
    )

    assert csrf_failure.status_code == 403
    assert credentials_failure.status_code == 401
    assert "Usuário ou senha inválidos." in credentials_failure.text
    assert "senha-incorreta" not in credentials_failure.text


def test_login_route_blocks_the_fifth_failure_and_audits_attempts(
    authentication_context: AuthenticationContext,
) -> None:
    client = authentication_context.client
    csrf_token = _login_csrf(client)

    responses = [
        client.post(
            "/login",
            data={
                "username": "admin",
                "password": "senha-incorreta",
                "csrf_token": csrf_token,
            },
        )
        for _ in range(5)
    ]
    correct_password_while_blocked = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "senha-ficticia",
            "csrf_token": csrf_token,
        },
    )

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 429]
    assert correct_password_while_blocked.status_code == 429
    assert "Muitas tentativas." in correct_password_while_blocked.text

    factory = create_session_factory(authentication_context.engine)
    with session_scope(factory) as session:
        attempts = list(session.scalars(select(LoginAttempt)))
        block_events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == "LOGIN_BLOCKED"))
        )
    assert len(attempts) == 6
    assert len(block_events) == 1


def test_login_session_logout_and_csrf_flow(
    authentication_context: AuthenticationContext,
) -> None:
    client = authentication_context.client
    _login(client)

    assert client.cookies.get(SESSION_COOKIE_NAME) is not None
    csrf_token = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf_token is not None

    home_response = client.get("/")
    invalid_logout = client.post("/logout", data={"csrf_token": "invalido"})
    logout_response = client.post(
        "/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert home_response.status_code == 200
    assert "admin" in home_response.text
    assert csrf_token in home_response.text
    assert invalid_logout.status_code == 403
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303

    factory = create_session_factory(authentication_context.engine)
    with session_scope(factory) as session:
        stored = session.scalar(select(SessionRecord))
        assert stored is not None
        assert stored.revoked_at is not None


def test_development_cookies_are_httponly_strict_and_not_secure(
    authentication_context: AuthenticationContext,
) -> None:
    client = authentication_context.client
    csrf_token = _login_csrf(client)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    session_header = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{SESSION_COOKIE_NAME}=")
    ).lower()
    assert "httponly" in session_header
    assert "samesite=strict" in session_header
    assert "secure" not in session_header
