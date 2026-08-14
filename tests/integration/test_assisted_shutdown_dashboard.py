from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import Job
from app.main import create_app
from app.shutdown.jobs import ShutdownJobKind


@pytest.fixture
def shutdown_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Engine]]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    with session_scope(create_session_factory(engine)) as session:
        create_administrator(session, "admin", "senha-ficticia")
    app = create_app(Settings(environment=AppEnvironment.TEST, manager_database=database_path))
    with TestClient(app, base_url="http://testserver") as client:
        yield client, engine
    engine.dispose()


def _login(client: TestClient) -> str:
    client.get("/login")
    login_csrf = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert login_csrf
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
    assert csrf
    return csrf


def test_shutdown_route_validates_csrf_confirmation_and_allowed_duration(
    shutdown_client: tuple[TestClient, Engine],
) -> None:
    client, _engine = shutdown_client
    csrf = _login(client)

    assert (
        client.post(
            "/dashboard/shutdown",
            data={"countdown_minutes": "5", "confirmation": "STOP", "csrf_token": "invalid"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/dashboard/shutdown",
            data={"countdown_minutes": "2", "confirmation": "STOP", "csrf_token": csrf},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/dashboard/shutdown",
            data={"countdown_minutes": "5", "confirmation": "wrong", "csrf_token": csrf},
        ).status_code
        == 400
    )


def test_shutdown_can_be_enqueued_and_cancelled_from_htmx_fragment(
    shutdown_client: tuple[TestClient, Engine],
) -> None:
    client, engine = shutdown_client
    csrf = _login(client)
    accepted = client.post(
        "/dashboard/shutdown",
        data={"countdown_minutes": "5", "confirmation": "STOP", "csrf_token": csrf},
    )
    assert accepted.status_code == 202
    assert 'data-job-status="PENDING"' in accepted.text
    assert ">Cancelar</button>" in accepted.text
    assert ">Forçar agora</button>" in accepted.text
    assert "hx-confirm" not in accepted.text
    assert 'data-confirm-title="Cancelar desligamento?"' in accepted.text
    assert 'data-confirm-title="Executar Stop agora?"' in accepted.text
    assert 'data-confirm-key="shutdown-cancel-1"' in accepted.text
    assert 'data-confirm-key="shutdown-now-1"' in accepted.text

    cancelled = client.post(
        "/dashboard/shutdown/jobs/1/cancel",
        data={"csrf_token": csrf},
    )
    assert cancelled.status_code == 200
    assert 'data-job-status="CANCELLED"' in cancelled.text
    with session_scope(create_session_factory(engine)) as session:
        assert session.scalar(select(Job.kind)) == ShutdownJobKind.ASSISTED.value


def test_conflicting_start_keeps_shutdown_progress_and_controls_visible(
    shutdown_client: tuple[TestClient, Engine],
) -> None:
    client, _engine = shutdown_client
    csrf = _login(client)
    accepted = client.post(
        "/dashboard/shutdown",
        data={"countdown_minutes": "5", "confirmation": "STOP", "csrf_token": csrf},
    )
    assert accepted.status_code == 202

    conflict = client.post(
        "/dashboard/lifecycle/START",
        data={"confirmation": "START", "csrf_token": csrf},
    )

    assert conflict.status_code == 200
    assert "Já existe uma ação do servidor em andamento." in conflict.text
    assert 'data-shutdown-job="1"' in conflict.text
    assert "Tempo restante: 300 s" in conflict.text
    assert ">Cancelar</button>" in conflict.text
    assert ">Forçar agora</button>" in conflict.text


def test_dashboard_recovers_active_shutdown_fragment_after_reload(
    shutdown_client: tuple[TestClient, Engine],
) -> None:
    client, _engine = shutdown_client
    csrf = _login(client)
    client.post(
        "/dashboard/shutdown",
        data={"countdown_minutes": "5", "confirmation": "STOP", "csrf_token": csrf},
    )

    home = client.get("/")
    active = client.get("/dashboard/active-job")

    assert 'hx-get="/dashboard/active-job"' in home.text
    assert active.status_code == 200
    assert 'data-shutdown-job="1"' in active.text
    assert ">Cancelar</button>" in active.text


def test_forced_routes_require_exact_two_level_confirmations(
    shutdown_client: tuple[TestClient, Engine],
) -> None:
    client, engine = shutdown_client
    csrf = _login(client)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        source = Job(
            kind=ShutdownJobKind.ASSISTED.value,
            status="FAILED",
            progress=100,
            coordination_key="PALWORLD_LIFECYCLE",
            result={"failure": "stop_failed"},
        )
        session.add(source)
        session.flush()
        source_id = source.id

    invalid = client.post(
        f"/dashboard/shutdown/jobs/{source_id}/force/SIGTERM",
        data={"confirmation": "forcar", "csrf_token": csrf},
    )
    assert invalid.status_code == 400
    accepted = client.post(
        f"/dashboard/shutdown/jobs/{source_id}/force/SIGTERM",
        data={"confirmation": "FORCAR", "csrf_token": csrf},
    )
    assert accepted.status_code == 202
    assert "Encerramento por SIGTERM" in accepted.text

    with session_scope(factory) as session:
        term = session.scalar(select(Job).where(Job.kind == ShutdownJobKind.FORCE_TERM.value))
        assert term is not None
        term.status = "FAILED"
        term.finished_at = term.created_at
        term_id = term.id
    kill_confirmation = client.get(f"/dashboard/shutdown/jobs/{term_id}")
    assert "hx-confirm" not in kill_confirmation.text
    assert 'data-confirm-title="Executar SIGKILL?"' in kill_confirmation.text
    assert 'data-confirm-tone="danger"' in kill_confirmation.text
    assert f'data-confirm-key="shutdown-sigkill-{term_id}"' in kill_confirmation.text
    invalid_kill = client.post(
        f"/dashboard/shutdown/jobs/{term_id}/force/SIGKILL",
        data={"confirmation": "FORCAR", "csrf_token": csrf},
    )
    accepted_kill = client.post(
        f"/dashboard/shutdown/jobs/{term_id}/force/SIGKILL",
        data={"confirmation": "SIGKILL", "csrf_token": csrf},
    )
    assert invalid_kill.status_code == 400
    assert accepted_kill.status_code == 202
