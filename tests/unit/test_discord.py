import json
from ipaddress import ip_address
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import AppEnvironment, Settings
from app.integrations.discord import (
    DISCORD_TIMEOUT_SECONDS,
    MAX_DISCORD_RESPONSE_BYTES,
    DiscordDeliveryError,
    DiscordDeliveryErrorKind,
    DiscordHttpResponse,
    DiscordMessage,
    FakeDiscordWebhook,
    OfficialDiscordWebhook,
    UnconfiguredDiscordWebhook,
    create_discord_webhook,
)

VALID_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/123456789012345678/placeholder-not-a-real-token"
)


class RecordingDiscordTransport:
    def __init__(self, response: DiscordHttpResponse | None = None) -> None:
        self.response = response or DiscordHttpResponse(204, b"")
        self.calls: list[tuple[str, dict[str, str], bytes, float]] = []
        self.error: Exception | None = None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> DiscordHttpResponse:
        self.calls.append((url, headers, body, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.response


def test_official_webhook_posts_safe_json_with_mentions_disabled() -> None:
    transport = RecordingDiscordTransport()
    webhook = OfficialDiscordWebhook(VALID_WEBHOOK_URL, transport=transport)

    webhook.send(DiscordMessage("Backup automático falhou."))

    assert len(transport.calls) == 1
    url, headers, body, timeout = transport.calls[0]
    assert url == VALID_WEBHOOK_URL
    assert headers["Content-Type"] == "application/json"
    assert timeout == DISCORD_TIMEOUT_SECONDS
    assert json.loads(body) == {
        "content": "Backup automático falhou.",
        "allowed_mentions": {"parse": []},
    }


@pytest.mark.parametrize(
    ("status_code", "kind", "transient"),
    [
        (429, DiscordDeliveryErrorKind.RATE_LIMITED, True),
        (500, DiscordDeliveryErrorKind.UNAVAILABLE, True),
        (403, DiscordDeliveryErrorKind.REJECTED, False),
        (400, DiscordDeliveryErrorKind.INVALID_RESPONSE, False),
    ],
)
def test_official_webhook_classifies_http_failures_without_exposing_url(
    status_code: int,
    kind: DiscordDeliveryErrorKind,
    transient: bool,
) -> None:
    transport = RecordingDiscordTransport(DiscordHttpResponse(status_code, b"remote detail"))
    webhook = OfficialDiscordWebhook(VALID_WEBHOOK_URL, transport=transport)

    with pytest.raises(DiscordDeliveryError) as raised:
        webhook.send(DiscordMessage("Evento controlado"))

    assert raised.value.kind is kind
    assert raised.value.transient is transient
    assert "placeholder" not in str(raised.value)
    assert "remote detail" not in str(raised.value)


def test_official_webhook_rejects_invalid_message_and_url() -> None:
    with pytest.raises(ValueError, match="endpoint HTTPS oficial"):
        OfficialDiscordWebhook("http://127.0.0.1/webhook")
    webhook = OfficialDiscordWebhook(VALID_WEBHOOK_URL, transport=RecordingDiscordTransport())

    with pytest.raises(ValueError, match="entre 1 e 2000"):
        webhook.send(DiscordMessage(""))
    with pytest.raises(ValueError, match="entre 1 e 2000"):
        webhook.send(DiscordMessage("x" * 2001))


@pytest.mark.parametrize(
    ("transport_error", "kind"),
    [
        (TimeoutError(), DiscordDeliveryErrorKind.TIMEOUT),
        (OSError(), DiscordDeliveryErrorKind.UNAVAILABLE),
    ],
)
def test_official_webhook_classifies_transport_failures(
    transport_error: Exception,
    kind: DiscordDeliveryErrorKind,
) -> None:
    transport = RecordingDiscordTransport()
    transport.error = transport_error
    webhook = OfficialDiscordWebhook(VALID_WEBHOOK_URL, transport=transport)

    with pytest.raises(DiscordDeliveryError) as raised:
        webhook.send(DiscordMessage("Evento controlado"))

    assert raised.value.kind is kind
    assert raised.value.transient


def test_official_webhook_rejects_oversized_response() -> None:
    transport = RecordingDiscordTransport(
        DiscordHttpResponse(204, b"x" * (MAX_DISCORD_RESPONSE_BYTES + 1))
    )
    webhook = OfficialDiscordWebhook(VALID_WEBHOOK_URL, transport=transport)

    with pytest.raises(DiscordDeliveryError) as raised:
        webhook.send(DiscordMessage("Evento controlado"))

    assert raised.value.kind is DiscordDeliveryErrorKind.INVALID_RESPONSE


def test_fake_is_complete_and_configurable_without_network() -> None:
    webhook = FakeDiscordWebhook()
    webhook.queue_failure(DiscordDeliveryErrorKind.TIMEOUT)

    with pytest.raises(DiscordDeliveryError, match="não pôde ser entregue"):
        webhook.send(DiscordMessage("primeira tentativa"))
    webhook.send(DiscordMessage("segunda tentativa"))

    assert [message.content for message in webhook.messages] == ["segunda tentativa"]


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_development_and_test_always_select_fake(environment: AppEnvironment) -> None:
    settings = Settings(environment=environment)

    assert isinstance(create_discord_webhook(settings), FakeDiscordWebhook)


def test_production_without_secret_uses_controlled_unconfigured_gateway() -> None:
    settings = Settings(
        environment=AppEnvironment.PRODUCTION,
        app_host=ip_address("127.0.0.1"),
        palworld_rest_username=SecretStr("usuario-ficticio"),
        palworld_rest_password=SecretStr("senha-ficticia"),
    )

    assert isinstance(create_discord_webhook(settings), UnconfiguredDiscordWebhook)


def test_only_worker_constructs_the_discord_webhook() -> None:
    consumers = []
    for path in Path("app").rglob("*.py"):
        if path.as_posix() == "app/integrations/discord.py":
            continue
        if "create_discord_webhook(" in path.read_text(encoding="utf-8"):
            consumers.append(path.as_posix())

    assert consumers == ["app/worker.py"]
