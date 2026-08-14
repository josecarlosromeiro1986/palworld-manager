from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.logs.service import LogCategory, LogEntry
from app.main import create_app


@dataclass
class RecordingLogSource:
    stream_calls: list[str | None] = field(default_factory=list)

    def history(self, limit: int) -> list[LogEntry]:
        assert limit in {100, 500, 1000}
        return [
            LogEntry(
                cursor="fake:history",
                occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                message="Player <script>alert('x')</script> connected",
                category=LogCategory.CONNECTION,
            ),
            LogEntry(
                cursor="fake:last",
                occurred_at=datetime(2026, 8, 14, 12, 1, tzinfo=UTC),
                message="WARNING: teste seguro",
                category=LogCategory.WARNING,
            ),
        ]

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]:
        self.stream_calls.append(after_cursor)
        sequence = 2 if after_cursor == "fake:live:1" else 1
        yield LogEntry(
            cursor=f"fake:live:{sequence}",
            occurred_at=datetime(2026, 8, 14, 12, 2, tzinfo=UTC),
            message="Evento ao vivo",
            category=LogCategory.NORMAL,
        )


@dataclass(frozen=True)
class LogsContext:
    client: TestClient
    engine: Engine
    source: RecordingLogSource


@pytest.fixture
def logs_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LogsContext]:
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
    source = RecordingLogSource()
    application.state.palworld_log_source = source
    with TestClient(application, base_url="http://testserver") as client:
        yield LogsContext(client=client, engine=engine, source=source)
    engine.dispose()


def login(client: TestClient) -> None:
    page = client.get("/login")
    assert page.status_code == 200
    csrf_token = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert csrf_token is not None
    response = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.parametrize("path", ["/logs", "/logs/stream"])
def test_log_routes_require_authentication(logs_context: LogsContext, path: str) -> None:
    response = logs_context.client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_log_page_has_history_filters_pause_autoscroll_and_copy(
    logs_context: LogsContext,
) -> None:
    login(logs_context.client)

    response = logs_context.client.get("/logs?lines=500")

    assert response.status_code == 200
    assert '<a href="/logs" class="nav-item" aria-current="page">' in response.text
    assert 'value="500" selected' in response.text
    assert "data-log-search" in response.text
    assert "data-log-category-filter" in response.text
    assert "data-log-pause" in response.text
    assert "data-log-autoscroll" in response.text
    assert "Copiar trecho" in response.text
    assert 'data-last-cursor="fake:last"' in response.text
    assert "&lt;script&gt;alert" in response.text
    assert "<script>alert('x')</script>" not in response.text


def test_log_page_rejects_non_allowlisted_history_limit(logs_context: LogsContext) -> None:
    login(logs_context.client)

    response = logs_context.client.get("/logs?lines=250")

    assert response.status_code == 422


def test_sse_reconnects_after_last_delivered_event_without_replaying_it(
    logs_context: LogsContext,
) -> None:
    login(logs_context.client)

    initial = logs_context.client.get("/logs/stream?cursor=fake:last")
    reconnected = logs_context.client.get(
        "/logs/stream?cursor=fake:stale",
        headers={"Last-Event-ID": "fake:live:1"},
    )

    assert initial.status_code == 200
    assert initial.headers["content-type"].startswith("text/event-stream")
    assert initial.headers["cache-control"] == "no-cache, no-store"
    assert initial.headers["x-accel-buffering"] == "no"
    assert "id: fake:live:1" in initial.text
    assert "retry: 2000" in initial.text
    assert "event: log" in initial.text
    assert '"message":"Evento ao vivo"' in initial.text
    assert reconnected.status_code == 200
    assert "id: fake:live:2" in reconnected.text
    assert "id: fake:live:1" not in reconnected.text
    assert logs_context.source.stream_calls == ["fake:last", "fake:live:1"]


def test_invalid_sse_cursor_never_reaches_log_source(logs_context: LogsContext) -> None:
    login(logs_context.client)

    response = logs_context.client.get("/logs/stream?cursor=--since%3Dnow")

    assert response.status_code == 400
    assert logs_context.source.stream_calls == []


def test_logs_are_not_duplicated_in_sqlite(logs_context: LogsContext) -> None:
    login(logs_context.client)
    logs_context.client.get("/logs")
    application = cast(FastAPI, logs_context.client.app)
    assert application.state.palworld_log_source is logs_context.source

    assert "palworld_logs" not in inspect(logs_context.engine).get_table_names()
    assert "log_entries" not in inspect(logs_context.engine).get_table_names()


def test_frontend_uses_native_eventsource_reconnection(logs_context: LogsContext) -> None:
    script = logs_context.client.get("/static/dist/app.js")

    assert script.status_code == 200
    assert "new EventSource(streamUrl)" in script.text
    assert 'eventSource.addEventListener("error"' in script.text
    assert "Reconectando ao streaming" in script.text
