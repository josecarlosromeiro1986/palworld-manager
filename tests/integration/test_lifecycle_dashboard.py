from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, Job
from app.lifecycle.service import create_lifecycle_executor
from app.lifecycle.worker import LifecycleJobWorker
from app.main import create_app


@pytest.fixture
def lifecycle_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Engine]]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")

    application = create_app(
        Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    )
    with TestClient(application, base_url="http://testserver") as client:
        yield client, engine
    engine.dispose()


def _login(client: TestClient) -> str:
    client.get("/login")
    login_csrf = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
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
    csrf_token = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf_token is not None
    return csrf_token


def test_dashboard_renders_confirmed_lifecycle_actions(
    lifecycle_context: tuple[TestClient, Engine],
) -> None:
    client, _engine = lifecycle_context
    csrf_token = _login(client)

    response = client.get("/")

    assert response.status_code == 200
    for action, question in (
        ("START", "Iniciar o servidor Palworld?"),
        ("RESTART", "Reiniciar o servidor Palworld?"),
    ):
        assert f'hx-post="/dashboard/lifecycle/{action}"' in response.text
        assert f'value="{action}"' in response.text
        assert "data-confirm" in response.text
        assert f'data-confirm-message="{question}"' in response.text
    assert 'hx-post="/dashboard/shutdown"' in response.text
    assert "hx-confirm" not in response.text
    assert 'data-confirm-tone="danger"' in response.text
    assert '<option value="5" selected>5 min</option>' in response.text
    assert csrf_token in response.text


def test_lifecycle_action_requires_authentication(
    lifecycle_context: tuple[TestClient, Engine],
) -> None:
    client, _engine = lifecycle_context

    response = client.post(
        "/dashboard/lifecycle/START",
        data={"confirmation": "START", "csrf_token": "invalido"},
    )

    assert response.status_code == 401


def test_lifecycle_action_requires_csrf_and_exact_confirmation(
    lifecycle_context: tuple[TestClient, Engine],
) -> None:
    client, _engine = lifecycle_context
    csrf_token = _login(client)

    invalid_csrf = client.post(
        "/dashboard/lifecycle/START",
        data={"confirmation": "START", "csrf_token": "invalido"},
    )
    invalid_confirmation = client.post(
        "/dashboard/lifecycle/START",
        data={"confirmation": "STOP", "csrf_token": csrf_token},
    )

    assert invalid_csrf.status_code == 403
    assert invalid_confirmation.status_code == 400


def test_lifecycle_action_enqueues_job_and_prevents_double_submit(
    lifecycle_context: tuple[TestClient, Engine],
) -> None:
    client, engine = lifecycle_context
    csrf_token = _login(client)

    accepted = client.post(
        "/dashboard/lifecycle/RESTART",
        data={"confirmation": "RESTART", "csrf_token": csrf_token},
    )
    conflict = client.post(
        "/dashboard/shutdown",
        data={"countdown_minutes": "5", "confirmation": "STOP", "csrf_token": csrf_token},
    )

    assert accepted.status_code == 202
    assert 'data-job-status="PENDING"' in accepted.text
    assert 'hx-trigger="every 1s"' in accepted.text
    assert conflict.status_code == 200
    assert "Já existe uma ação" in conflict.text
    assert 'data-lifecycle-job="1"' in conflict.text
    assert 'data-job-status="PENDING"' in conflict.text
    assert 'hx-get="/dashboard/lifecycle/jobs/1"' in conflict.text

    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        jobs = list(session.scalars(select(Job)))
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.action == "RESTART_SERVER_REQUESTED")
            )
        )
    assert len(jobs) == 1
    assert len(events) == 1


def test_worker_completion_updates_shared_fake_health(
    lifecycle_context: tuple[TestClient, Engine],
) -> None:
    client, engine = lifecycle_context
    csrf_token = _login(client)
    accepted = client.post(
        "/dashboard/lifecycle/START",
        data={"confirmation": "START", "csrf_token": csrf_token},
    )
    assert accepted.status_code == 202

    factory = create_session_factory(engine)
    application = cast(FastAPI, client.app)
    settings = cast(Settings, application.state.settings)
    worker = LifecycleJobWorker(
        factory,
        create_lifecycle_executor(settings, factory),
        worker_id="worker-test",
    )

    assert worker.process_next() is True

    job_fragment = client.get("/dashboard/lifecycle/jobs/1")
    health_fragment = client.get("/dashboard/palworld-health")
    assert 'data-job-status="SUCCEEDED"' in job_fragment.text
    assert 'data-health-state="ONLINE"' in health_fragment.text


def test_lifecycle_job_status_is_private_and_scoped_to_lifecycle_jobs(
    lifecycle_context: tuple[TestClient, Engine],
) -> None:
    client, engine = lifecycle_context
    assert client.get("/dashboard/lifecycle/jobs/1", follow_redirects=False).status_code == 303
    _login(client)

    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        unrelated = Job(kind="BACKUP", status="PENDING")
        session.add(unrelated)
        session.flush()
        unrelated_id = unrelated.id

    assert client.get(f"/dashboard/lifecycle/jobs/{unrelated_id}").status_code == 404
    assert client.get("/dashboard/lifecycle/jobs/999999").status_code == 404
