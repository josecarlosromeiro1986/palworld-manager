from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent, Job, MaintenanceLock
from app.health.palworld import PalworldHealthState
from app.host_power.jobs import HostPowerJobExecutor
from app.lifecycle.fake import PersistentFakePalworldEnvironment
from app.lifecycle.service import create_lifecycle_executor
from app.lifecycle.worker import LifecycleJobWorker
from app.main import create_app
from app.shutdown.service import (
    AssistedShutdownResult,
    CountdownControl,
    ShutdownOutcome,
    create_shutdown_executors,
)
from app.system.host_power import FakeHostPowerController, HostPowerAction


@dataclass(frozen=True, slots=True)
class HostPowerContext:
    client: TestClient
    engine: Engine
    factory: sessionmaker[Session]
    settings: Settings


@pytest.fixture
def host_power_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[HostPowerContext]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        create_administrator(session, "admin", "senha-ficticia")
    settings = Settings(environment=AppEnvironment.TEST, manager_database=database_path)
    application = create_app(settings)
    with TestClient(application, base_url="http://testserver") as client:
        yield HostPowerContext(client, engine, factory, settings)
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
    csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf is not None
    return csrf


def test_dashboard_renders_strong_host_power_controls(
    host_power_context: HostPowerContext,
) -> None:
    csrf = _login(host_power_context.client)

    response = host_power_context.client.get("/")

    assert response.status_code == 200
    assert 'hx-post="/host-power/REBOOT"' in response.text
    assert 'hx-post="/host-power/SHUTDOWN"' in response.text
    assert "Digite REINICIAR UBUNTU" in response.text
    assert "Digite DESLIGAR UBUNTU" in response.text
    assert "O painel e o worker ficarão indisponíveis" in response.text
    assert 'data-confirm-source="host-power-confirmation-reboot"' in response.text
    assert 'data-confirm-source="host-power-confirmation-shutdown"' in response.text
    assert "hx-confirm" not in response.text
    assert csrf in response.text


def test_host_power_requires_authentication_csrf_confirmation_and_closed_action(
    host_power_context: HostPowerContext,
) -> None:
    unauthenticated = host_power_context.client.post(
        "/host-power/REBOOT",
        data={"confirmation": "REINICIAR UBUNTU", "csrf_token": "invalido"},
    )
    assert unauthenticated.status_code == 401
    csrf = _login(host_power_context.client)

    invalid_csrf = host_power_context.client.post(
        "/host-power/REBOOT",
        data={"confirmation": "REINICIAR UBUNTU", "csrf_token": "invalido"},
    )
    invalid_confirmation = host_power_context.client.post(
        "/host-power/REBOOT",
        data={"confirmation": "REINICIAR", "csrf_token": csrf},
    )
    invalid_action = host_power_context.client.post(
        "/host-power/reboot%3Bshutdown",
        data={"confirmation": "REINICIAR UBUNTU", "csrf_token": csrf},
    )

    assert invalid_csrf.status_code == 403
    assert invalid_confirmation.status_code == 400
    assert "Digite REINICIAR UBUNTU" in invalid_confirmation.text
    assert invalid_action.status_code == 422
    with session_scope(host_power_context.factory) as session:
        assert list(session.scalars(select(Job))) == []


def test_host_power_enqueues_once_with_lock_and_audit(
    host_power_context: HostPowerContext,
) -> None:
    csrf = _login(host_power_context.client)

    accepted = host_power_context.client.post(
        "/host-power/REBOOT",
        data={"confirmation": "REINICIAR UBUNTU", "csrf_token": csrf},
    )
    conflict = host_power_context.client.post(
        "/host-power/SHUTDOWN",
        data={"confirmation": "DESLIGAR UBUNTU", "csrf_token": csrf},
    )

    assert accepted.status_code == 202
    assert 'data-job-status="PENDING"' in accepted.text
    assert 'hx-get="/host-power/jobs/1"' in accepted.text
    assert conflict.status_code == 409
    assert "Já existe uma ação de energia" in conflict.text
    with session_scope(host_power_context.factory) as session:
        jobs = list(session.scalars(select(Job)))
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "HOST_REBOOT_REQUESTED")
        )
        assert len(jobs) == 1
        assert jobs[0].requires_maintenance_lock is True
        assert jobs[0].coordination_key == "HOST_POWER"
        assert jobs[0].is_cancellable is False
        assert audit is not None
        assert audit.user_id is not None
        assert audit.target == "Ubuntu"


def test_worker_stops_palworld_then_requests_host_power(
    host_power_context: HostPowerContext,
) -> None:
    csrf = _login(host_power_context.client)
    accepted = host_power_context.client.post(
        "/host-power/REBOOT",
        data={"confirmation": "REINICIAR UBUNTU", "csrf_token": csrf},
    )
    assert accepted.status_code == 202

    environment = PersistentFakePalworldEnvironment(host_power_context.factory)
    environment.start()
    assisted, _forced = create_shutdown_executors(
        host_power_context.settings,
        host_power_context.factory,
    )
    power = FakeHostPowerController()
    worker = LifecycleJobWorker(
        host_power_context.factory,
        create_lifecycle_executor(host_power_context.settings, host_power_context.factory),
        worker_id="worker-test",
        host_power_executor=HostPowerJobExecutor(
            host_power_context.factory,
            environment,
            assisted,
            power,
        ),
    )

    assert worker.process_next() is True
    assert environment.check().state is PalworldHealthState.OFFLINE
    assert power.requests == [HostPowerAction.REBOOT]
    with session_scope(host_power_context.factory) as session:
        job = session.get_one(Job, 1)
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "HOST_REBOOT"))
        assert job.status == "SUCCEEDED"
        assert job.result is not None
        assert job.result["palworld_handling"] == "stopped"
        assert job.result["host_command_requested"] is True
        assert session.scalar(select(MaintenanceLock)) is None
        assert audit is not None
        assert audit.result == "SUCCESS"

    fragment = host_power_context.client.get("/host-power/jobs/1")
    assert fragment.status_code == 200
    assert 'data-job-status="SUCCEEDED"' in fragment.text
    assert "O comando foi aceito" in fragment.text


class FailedSafeShutdown:
    def execute(
        self,
        countdown_minutes: int,
        stop_timeout_seconds: int,
        control: CountdownControl,
    ) -> AssistedShutdownResult:
        del countdown_minutes, stop_timeout_seconds, control
        return AssistedShutdownResult(
            ShutdownOutcome.FAILED,
            online_players=None,
            timed_out=False,
            final_state=PalworldHealthState.DEGRADED,
            failure="communication_failed",
        )


def test_failed_palworld_shutdown_never_requests_host_power(
    host_power_context: HostPowerContext,
) -> None:
    csrf = _login(host_power_context.client)
    response = host_power_context.client.post(
        "/host-power/SHUTDOWN",
        data={"confirmation": "DESLIGAR UBUNTU", "csrf_token": csrf},
    )
    assert response.status_code == 202
    environment = PersistentFakePalworldEnvironment(host_power_context.factory)
    environment.start()
    power = FakeHostPowerController()
    worker = LifecycleJobWorker(
        host_power_context.factory,
        create_lifecycle_executor(host_power_context.settings, host_power_context.factory),
        worker_id="worker-test",
        host_power_executor=HostPowerJobExecutor(
            host_power_context.factory,
            environment,
            FailedSafeShutdown(),
            power,
        ),
    )

    assert worker.process_next() is True
    assert power.requests == []
    with session_scope(host_power_context.factory) as session:
        job = session.get_one(Job, 1)
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "HOST_SHUTDOWN"))
        assert job.status == "FAILED"
        assert job.result is not None
        assert job.result["failure"] == "palworld_shutdown_failed"
        assert audit is not None
        assert audit.result == "FAILURE"


def test_host_power_job_status_is_private_and_scoped(
    host_power_context: HostPowerContext,
) -> None:
    assert (
        host_power_context.client.get("/host-power/jobs/1", follow_redirects=False).status_code
        == 303
    )
    _login(host_power_context.client)
    with session_scope(host_power_context.factory) as session:
        unrelated = Job(kind="UNRELATED", status="PENDING")
        session.add(unrelated)
        session.flush()
        unrelated_id = unrelated.id

    assert host_power_context.client.get(f"/host-power/jobs/{unrelated_id}").status_code == 404
    assert host_power_context.client.get("/host-power/jobs/999999").status_code == 404
