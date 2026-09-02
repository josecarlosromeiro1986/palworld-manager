from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.roles import UserRole
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import Job, User
from app.main import create_app
from app.users.service import create_user


@dataclass
class RoleContext:
    client: TestClient
    engine: Engine


@pytest.fixture
def role_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RoleContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        administrator = create_administrator(session, "Admin", "senha-admin")
        create_user(
            session,
            "Operador",
            "senha-temporaria",
            UserRole.USER,
            actor_user_id=administrator.id,
        )
    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        yield RoleContext(client, engine)
    engine.dispose()


def _login(
    client: TestClient,
    username: str,
    password: str,
) -> str:
    page = client.get("/login")
    assert page.status_code == 200
    token = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert token is not None
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"]


def _session_csrf(client: TestClient) -> str:
    token = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert token is not None
    return token


def test_temporary_password_blocks_all_but_account_and_logout(
    role_context: RoleContext,
) -> None:
    client = role_context.client
    assert _login(client, "operador", "senha-temporaria") == ("/account?password_change_required=1")

    blocked_page = client.get("/", follow_redirects=False)
    blocked_action = client.post(
        "/dashboard/lifecycle/START",
        data={"confirmation": "START", "csrf_token": _session_csrf(client)},
    )
    account = client.get("/account")

    assert blocked_page.status_code == 303
    assert blocked_page.headers["location"] == "/account?password_change_required=1"
    assert blocked_action.status_code == 403
    assert account.status_code == 200
    assert "Altere a senha temporária" in account.text

    changed = client.post(
        "/account/password",
        data={
            "csrf_token": _session_csrf(client),
            "current_password": "senha-temporaria",
            "new_password": "senha-definitiva",
            "new_password_confirmation": "senha-definitiva",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/login?password_changed=1"
    assert _login(client, "OPERADOR", "senha-definitiva") == "/"


def test_user_sees_only_dashboard_and_allowed_lifecycle_actions(
    role_context: RoleContext,
) -> None:
    client = role_context.client
    _login(client, "operador", "senha-temporaria")
    client.post(
        "/account/password",
        data={
            "csrf_token": _session_csrf(client),
            "current_password": "senha-temporaria",
            "new_password": "senha-definitiva",
            "new_password_confirmation": "senha-definitiva",
        },
    )
    _login(client, "operador", "senha-definitiva")
    csrf_token = _session_csrf(client)

    home = client.get("/")
    forbidden_gets = [
        client.get(path, follow_redirects=False)
        for path in ("/users", "/players", "/backups", "/manager-settings", "/diagnostics")
    ]
    forbidden_posts = [
        client.post("/backups", data={"csrf_token": csrf_token}),
        client.post(
            "/host-power/REBOOT",
            data={"csrf_token": csrf_token, "confirmation": "REINICIAR UBUNTU"},
        ),
        client.post(
            "/dashboard/shutdown/jobs/1/force/SIGTERM",
            data={"csrf_token": csrf_token, "confirmation": "FORCAR"},
        ),
    ]
    start = client.post(
        "/dashboard/lifecycle/START",
        data={"csrf_token": csrf_token, "confirmation": "START"},
    )

    assert home.status_code == 200
    assert "Dashboard" in home.text
    assert "Jogadores" not in home.text
    assert "Backup agora" not in home.text
    assert "Energia do Ubuntu" not in home.text
    assert all(response.status_code == 403 for response in forbidden_gets)
    assert all(response.status_code == 403 for response in forbidden_posts)
    assert start.status_code == 202
    factory = create_session_factory(role_context.engine)
    with session_scope(factory) as session:
        job = session.scalar(select(Job).where(Job.kind == "PALWORLD_START"))
        user = session.scalar(select(User).where(User.username_key == "operador"))
        assert job is not None and user is not None
        assert job.requested_by_user_id == user.id


def test_administrator_can_create_user_but_cannot_manage_self(
    role_context: RoleContext,
) -> None:
    client = role_context.client
    assert _login(client, "admin", "senha-admin") == "/"
    csrf_token = _session_csrf(client)
    page = client.get("/users")
    created = client.post(
        "/users",
        data={
            "csrf_token": csrf_token,
            "username": "NovoUser",
            "role": "USER",
            "temporary_password": "senha-temporaria",
            "temporary_password_confirmation": "senha-temporaria",
        },
        follow_redirects=False,
    )
    factory = create_session_factory(role_context.engine)
    with session_scope(factory) as session:
        administrator = session.scalar(select(User).where(User.username_key == "admin"))
        created_user = session.scalar(select(User).where(User.username_key == "novouser"))
        assert administrator is not None and created_user is not None
        administrator_id = administrator.id
        assert created_user.password_change_required is True
    self_change = client.post(
        f"/users/{administrator_id}/status",
        data={"csrf_token": csrf_token, "active": "false"},
    )

    assert page.status_code == 200
    assert "Criar usuário" in page.text
    assert created.status_code == 303
    assert self_change.status_code == 400
    assert "papel e status próprios" in self_change.text
