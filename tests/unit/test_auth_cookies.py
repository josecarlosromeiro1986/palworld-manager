from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

from fastapi import Response

from app.auth.cookies import SESSION_COOKIE_NAME, set_session_cookies
from app.auth.sessions import IssuedSession
from app.config import AppEnvironment, Settings


def test_production_session_cookie_is_secure_httponly_and_strict() -> None:
    response = Response()
    settings = Settings(
        environment=AppEnvironment.PRODUCTION,
        app_host=ip_address("127.0.0.1"),
        manager_database=Path("/var/lib/palworld-manager/manager.db"),
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
