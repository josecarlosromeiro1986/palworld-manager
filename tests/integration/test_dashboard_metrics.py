from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.dashboard.metrics import HostMetricsService, RawHostMetrics
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.main import create_app


class FixedMetricsSource:
    def read(self) -> RawHostMetrics:
        return RawHostMetrics(
            cpu_percent=12.5,
            memory_percent=37.5,
            memory_used_bytes=4 * 1024**3,
            memory_total_bytes=16 * 1024**3,
            disk_percent=62.5,
            disk_used_bytes=100 * 1024**3,
            disk_total_bytes=250 * 1024**3,
            disk_free_bytes=150 * 1024**3,
            network_received_bytes=1_000,
            network_sent_bytes=500,
        )


@pytest.fixture
def metrics_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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
    application.state.metrics_service = HostMetricsService(
        FixedMetricsSource(),
        clock=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )
    with TestClient(application, base_url="http://testserver") as client:
        yield client

    table_names = inspect(engine).get_table_names()
    assert all("metric" not in table_name for table_name in table_names)
    engine.dispose()


def login(client: TestClient) -> None:
    login_page = client.get("/login")
    csrf_token = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert csrf_token is not None
    assert login_page.status_code == 200
    response = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_metrics_fragment_requires_authentication(metrics_client: TestClient) -> None:
    response = metrics_client.get("/dashboard/metrics", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_polls_and_renders_host_metrics(metrics_client: TestClient) -> None:
    login(metrics_client)

    dashboard = metrics_client.get("/")
    metrics = metrics_client.get("/dashboard/metrics")

    assert dashboard.status_code == 200
    assert 'hx-get="/dashboard/metrics"' in dashboard.text
    assert 'hx-trigger="load, every 5s"' in dashboard.text
    assert "/static/dist/vendor/chart.umd.js" in dashboard.text
    assert metrics_client.get("/static/dist/vendor/chart.umd.js").status_code == 200
    assert metrics.status_code == 200
    assert "12.5%" in metrics.text
    assert "37.5%" in metrics.text
    assert "150.0 GiB livres de 250.0 GiB" in metrics.text
    assert "data-resource-chart" in metrics.text
    assert "data-network-chart" in metrics.text
    assert '"cpu": [12.5]' in metrics.text
