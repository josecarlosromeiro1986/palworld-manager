from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

from fastapi import Response
from pydantic import SecretStr

from app.auth.cookies import SESSION_COOKIE_NAME, set_session_cookies
from app.auth.middleware import _is_public_path
from app.auth.sessions import IssuedSession
from app.config import AppEnvironment, Settings


def test_production_session_cookie_is_secure_httponly_and_strict() -> None:
    response = Response()
    settings = Settings(
        environment=AppEnvironment.PRODUCTION,
        app_host=ip_address("127.0.0.1"),
        manager_database=Path("/var/lib/palworld-manager/manager.db"),
        palworld_rest_username=SecretStr("usuario-ficticio"),
        palworld_rest_password=SecretStr("senha-ficticia"),
    )
    issued = IssuedSession(
        session_token="token-ficticio",
        csrf_token="csrf-ficticio",
        expires_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    set_session_cookies(response, issued, settings)

    session_header = next(
        value
        for value in response.headers.getlist("set-cookie")
        if value.startswith(f"{SESSION_COOKIE_NAME}=")
    ).lower()
    assert "secure" in session_header
    assert "httponly" in session_header
    assert "samesite=strict" in session_header


def test_only_expected_unauthenticated_paths_are_public() -> None:
    assert _is_public_path("/health")
    assert _is_public_path("/login")
    assert _is_public_path("/static/dist/app.css")
    assert not _is_public_path("/static-private")
    assert not _is_public_path("/")
