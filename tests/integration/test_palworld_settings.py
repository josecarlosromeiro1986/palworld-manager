from collections.abc import Iterator
from dataclasses import dataclass
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
from app.db.models import AuditEvent
from app.main import create_app
from app.palworld_settings.service import PalworldSettingsService, PalworldSettingsSnapshot
from app.palworld_settings.storage import (
    FakePalworldSettingsStorage,
    SettingsStorageErrorKind,
)


@dataclass(frozen=True)
class SettingsContext:
    client: TestClient
    engine: Engine
    storage: FakePalworldSettingsStorage
    service: PalworldSettingsService


@pytest.fixture
def settings_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SettingsContext]:
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
    storage = cast(FakePalworldSettingsStorage, application.state.palworld_settings_storage)
    service = cast(PalworldSettingsService, application.state.palworld_settings_service)
    with TestClient(application, base_url="http://testserver") as client:
        yield SettingsContext(client=client, engine=engine, storage=storage, service=service)
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


def _editable_form(snapshot: PalworldSettingsSnapshot, csrf: str) -> dict[str, str]:
    data = {"csrf_token": csrf, "version": snapshot.version}
    for category in snapshot.categories:
        for field in category.fields:
            if field.editable:
                assert field.value is not None
                data[f"setting__{field.key}"] = field.value
    return data


def test_settings_page_is_private_and_masks_sensitive_values(
    settings_context: SettingsContext,
) -> None:
    private = settings_context.client.get("/palworld-settings", follow_redirects=False)
    assert private.status_code == 303

    _login(settings_context.client)
    response = settings_context.client.get("/palworld-settings")

    assert response.status_code == 200
    assert "Configurações do Palworld" in response.text
    assert "Referência oficial 1.0.3" in response.text
    assert "FutureSetting" in response.text
    assert "valor-fake-nao-exibir" not in response.text
    assert "Valor sensível ocultado" in response.text
    assert 'action="/palworld-settings"' in response.text
    assert "data-confirm" in response.text
    assert "hx-confirm" not in response.text


def test_save_requires_csrf_creates_backup_and_audits_only_field_names(
    settings_context: SettingsContext,
) -> None:
    csrf = _login(settings_context.client)
    snapshot = settings_context.service.load()
    form = _editable_form(snapshot, csrf)
    form["setting__ServerName"] = 'Servidor "Principal"'

    invalid_csrf = dict(form)
    invalid_csrf["csrf_token"] = "invalido"
    rejected = settings_context.client.post("/palworld-settings", data=invalid_csrf)
    success = settings_context.client.post("/palworld-settings", data=form)

    assert rejected.status_code == 403
    assert success.status_code == 200
    assert "Configurações salvas com backup" in success.text
    assert "Restart necessário" in success.text
    assert 'hx-post="/dashboard/lifecycle/RESTART"' in success.text
    assert 'name="confirmation" value="RESTART"' in success.text
    assert 'data-confirm-title="Reiniciar servidor agora?"' in success.text
    assert len(settings_context.storage.backups) == 1
    backup_name, backup_content = settings_context.storage.backups[0]
    assert backup_name in success.text
    assert 'ServerName="Servidor de desenvolvimento"' in backup_content
    assert 'ServerName="Servidor \\"Principal\\""' in settings_context.storage.content
    assert 'FutureSetting=(Mode="Preserve,Me")' in settings_context.storage.content
    assert 'AdminPassword="valor-fake-nao-exibir"' in settings_context.storage.content

    with session_scope(create_session_factory(settings_context.engine)) as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "PALWORLD_SETTINGS_UPDATE")
        )
    assert event is not None
    assert event.result == "SUCCESS"
    assert event.target == "PalWorldSettings.ini"
    assert event.details == {
        "changed_fields": ["ServerName"],
        "schema_version": "1.0.3",
        "backup_name": backup_name,
    }
    assert "Principal" not in str(event.details)
    assert "valor-fake-nao-exibir" not in str(event.details)


def test_unknown_or_invalid_fields_never_modify_ini(settings_context: SettingsContext) -> None:
    csrf = _login(settings_context.client)
    snapshot = settings_context.service.load()
    original = settings_context.storage.content
    unexpected = _editable_form(snapshot, csrf)
    unexpected["setting__FutureSetting"] = "apagado"

    unexpected_response = settings_context.client.post(
        "/palworld-settings",
        data=unexpected,
    )
    invalid = _editable_form(snapshot, csrf)
    invalid["setting__RCONPort"] = "70000"
    invalid_response = settings_context.client.post("/palworld-settings", data=invalid)

    assert unexpected_response.status_code == 400
    assert invalid_response.status_code == 400
    assert "campos enviados não correspondem" in unexpected_response.text
    assert "menor ou igual a 65535" in invalid_response.text
    assert settings_context.storage.content == original
    assert settings_context.storage.backups == []


def test_stale_page_is_rejected_and_external_failure_is_safe(
    settings_context: SettingsContext,
) -> None:
    csrf = _login(settings_context.client)
    snapshot = settings_context.service.load()
    stale_form = _editable_form(snapshot, csrf)
    settings_context.storage.content = settings_context.storage.content.replace(
        'ServerDescription="Ambiente simulado"',
        'ServerDescription="Mudança externa"',
    )

    conflict = settings_context.client.post("/palworld-settings", data=stale_form)

    assert conflict.status_code == 409
    assert "alterado depois da abertura" in conflict.text
    assert settings_context.storage.backups == []

    fresh = settings_context.service.load()
    failure_form = _editable_form(fresh, csrf)
    failure_form["setting__ServerName"] = "Não salvar"
    settings_context.storage.set_error(SettingsStorageErrorKind.PERMISSION)
    failure = settings_context.client.post("/palworld-settings", data=failure_form)

    assert failure.status_code == 503
    assert "não possui permissão" in failure.text
    assert "valor-fake-nao-exibir" not in failure.text
    with session_scope(create_session_factory(settings_context.engine)) as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "PALWORLD_SETTINGS_UPDATE")
                .order_by(AuditEvent.id)
            )
        )
    assert [event.result for event in events] == ["FAILURE", "FAILURE"]
    assert [event.details["error_kind"] for event in events if event.details] == [
        "conflict",
        "permission",
    ]


def test_test_environment_uses_fake_without_touching_configured_path(
    settings_context: SettingsContext,
    tmp_path: Path,
) -> None:
    application = cast(FastAPI, settings_context.client.app)
    configured_path = cast(Settings, application.state.settings).palworld_settings

    assert isinstance(application.state.palworld_settings_storage, FakePalworldSettingsStorage)
    assert configured_path != tmp_path / "PalWorldSettings.ini"
    assert not configured_path.exists()
