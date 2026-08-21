from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def clean_discord_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    yield


def test_discord_webhook_is_optional() -> None:
    assert Settings().discord_webhook_url is None


@pytest.mark.parametrize(
    "webhook_url",
    [
        "http://discord.com/api/webhooks/123/token",
        "https://example.invalid/api/webhooks/123/token",
        "https://discord.com/api/webhooks/123/token?wait=true",
        "https://discord.com/api/webhooks/not-an-id/token",
    ],
)
def test_production_rejects_unsafe_discord_webhook(
    webhook_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("PALWORLD_REST_USERNAME", "usuario-ficticio")
    monkeypatch.setenv("PALWORLD_REST_PASSWORD", "senha-ficticia")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", webhook_url)

    with pytest.raises(ValidationError, match="endpoint HTTPS oficial"):
        Settings()


def test_empty_discord_webhook_is_optional_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("PALWORLD_REST_USERNAME", "usuario-ficticio")
    monkeypatch.setenv("PALWORLD_REST_PASSWORD", "senha-ficticia")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")

    assert Settings().discord_webhook_url is None


def test_webhook_is_masked_by_settings_representation(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "placeholder-not-a-real-token"
    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL",
        f"https://discord.com/api/webhooks/123456789012345678/{marker}",
    )

    settings = Settings()

    assert settings.discord_webhook_url is not None
    assert marker not in repr(settings)
