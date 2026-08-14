from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from app.auth.cookies import LOGIN_CSRF_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME
from app.auth.service import create_administrator
from app.config import AppEnvironment, Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AuditEvent
from app.integrations.palworld_rest import (
    FakePalworldRestClient,
    PalworldPlayer,
    PalworldRestErrorKind,
)
from app.main import create_app
from app.players.service import ManualPlayersService


@dataclass(frozen=True)
class PlayersContext:
    client: TestClient
    engine: Engine
    rest: FakePalworldRestClient


@pytest.fixture
def players_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PlayersContext]:
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
    rest = FakePalworldRestClient(
        players=(
            PalworldPlayer(
                name="Jogador <script>",
                account_name="conta-teste",
                player_id="player-id",
                user_id="steam-user-id",
                ip="127.0.0.1",
                ping=7.25,
                location_x=10.0,
                location_y=20.0,
                level=42,
                building_count=3,
            ),
        )
    )
    application.state.palworld_rest_client = rest
    application.state.players_service = ManualPlayersService(
        rest,
        clock=lambda: datetime(2026, 8, 14, 15, 30, tzinfo=UTC),
    )
    with TestClient(application, base_url="http://testserver") as client:
        yield PlayersContext(client=client, engine=engine, rest=rest)
    engine.dispose()


def _login(client: TestClient) -> str:
    client.get("/login")
    login_csrf = client.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    assert login_csrf is not None
    response = client.post(
        "/login",
        data={"username": "admin", "password": "senha-ficticia", "csrf_token": login_csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    csrf = client.cookies.get(SESSION_CSRF_COOKIE_NAME)
    assert csrf is not None
    return csrf


def test_players_page_requires_authentication(players_context: PlayersContext) -> None:
    response = players_context.client.get("/players", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert players_context.rest.player_queries == 0


def test_players_are_never_polled_and_only_refresh_updates_memory_cache(
    players_context: PlayersContext,
) -> None:
    csrf = _login(players_context.client)

    initial = players_context.client.get("/players")
    second_read = players_context.client.get("/players")

    assert initial.status_code == 200
    assert "Ainda não consultado" in initial.text
    assert "hx-trigger" not in initial.text
    assert second_read.status_code == 200
    assert players_context.rest.player_queries == 0

    refreshed = players_context.client.post(
        "/players/refresh",
        data={"csrf_token": csrf},
    )
    cached_read = players_context.client.get("/players")

    assert refreshed.status_code == 200
    assert "Jogador &lt;script&gt;" in refreshed.text
    assert "conta-teste" in refreshed.text
    assert "player-id" in refreshed.text
    assert "steam-user-id" in refreshed.text
    assert "42" in refreshed.text
    assert "7.2 ms" in refreshed.text
    assert cached_read.status_code == 200
    assert players_context.rest.player_queries == 1
    table_names = inspect(players_context.engine).get_table_names()
    assert "online_players" not in table_names
    assert "player_cache" not in table_names


def test_player_refresh_requires_csrf_and_preserves_last_valid_cache_on_error(
    players_context: PlayersContext,
) -> None:
    csrf = _login(players_context.client)
    invalid_csrf = players_context.client.post(
        "/players/refresh",
        data={"csrf_token": "invalido"},
    )
    assert invalid_csrf.status_code == 403
    assert players_context.rest.player_queries == 0

    players_context.client.post("/players/refresh", data={"csrf_token": csrf})
    players_context.rest.set_error(PalworldRestErrorKind.TIMEOUT)
    failed = players_context.client.post("/players/refresh", data={"csrf_token": csrf})

    assert failed.status_code == 503
    assert "não respondeu dentro do tempo limite" in failed.text
    assert "Jogador &lt;script&gt;" in failed.text
    assert players_context.rest.player_queries == 2


def test_announcement_requires_exact_confirmation_csrf_and_audits_result(
    players_context: PlayersContext,
) -> None:
    csrf = _login(players_context.client)
    message = "Olá, jogadores!\nReinício em breve."

    invalid_csrf = players_context.client.post(
        "/players/announce",
        data={"message": message, "confirmation": message, "csrf_token": "invalido"},
    )
    mismatch = players_context.client.post(
        "/players/announce",
        data={"message": message, "confirmation": f"{message} ", "csrf_token": csrf},
    )
    success = players_context.client.post(
        "/players/announce",
        data={"message": message, "confirmation": message, "csrf_token": csrf},
    )

    assert invalid_csrf.status_code == 403
    assert mismatch.status_code == 400
    assert "repetir exatamente" in mismatch.text
    assert success.status_code == 200
    assert "Anúncio enviado e auditado com sucesso" in success.text
    assert players_context.rest.announcements == [message]

    with Session(players_context.engine) as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "PALWORLD_ANNOUNCEMENT")
                .order_by(AuditEvent.id)
            )
        )
    assert [event.result for event in events] == ["FAILURE", "SUCCESS"]
    assert all(event.user_id is not None for event in events)
    assert all(event.details == {"message": message} for event in events)


def test_announcement_confirmation_uses_accessible_application_modal(
    players_context: PlayersContext,
) -> None:
    _login(players_context.client)

    page = players_context.client.get("/players")
    script = players_context.client.get("/static/dist/app.js")

    assert page.status_code == 200
    assert "<dialog" in page.text
    assert "data-announcement-modal" in page.text
    assert 'aria-labelledby="announcement-modal-title"' in page.text
    assert "data-announcement-preview" in page.text
    assert "data-announcement-modal-cancel" in page.text
    assert "data-announcement-modal-confirm" in page.text
    assert script.status_code == 200
    assert "modal.showModal()" in script.text
    assert "form.requestSubmit()" in script.text
    assert "window.confirm" not in script.text


def test_failed_announcement_is_safe_and_audited(players_context: PlayersContext) -> None:
    csrf = _login(players_context.client)
    players_context.rest.set_error(PalworldRestErrorKind.UNAUTHORIZED)

    response = players_context.client.post(
        "/players/announce",
        data={"message": "Teste", "confirmation": "Teste", "csrf_token": csrf},
    )

    assert response.status_code == 503
    assert "autenticação da API" in response.text
    assert "senha-ficticia" not in response.text
    assert players_context.rest.announcements == []
    with Session(players_context.engine) as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "PALWORLD_ANNOUNCEMENT")
        )
    assert event is not None
    assert event.result == "FAILURE"
    assert event.details == {"message": "Teste", "error_kind": "unauthorized"}
