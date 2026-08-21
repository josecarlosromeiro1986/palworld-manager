import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import AppEnvironment, Settings, validate_discord_webhook_url

DISCORD_TIMEOUT_SECONDS = 5.0
MAX_DISCORD_RESPONSE_BYTES = 64 * 1024


class DiscordDeliveryErrorKind(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INTERRUPTED = "INTERRUPTED"
    REJECTED = "REJECTED"
    INVALID_RESPONSE = "INVALID_RESPONSE"


TRANSIENT_DISCORD_ERRORS = frozenset(
    {
        DiscordDeliveryErrorKind.RATE_LIMITED,
        DiscordDeliveryErrorKind.TIMEOUT,
        DiscordDeliveryErrorKind.UNAVAILABLE,
    }
)


class DiscordDeliveryError(RuntimeError):
    def __init__(self, kind: DiscordDeliveryErrorKind) -> None:
        self.kind = kind
        super().__init__("A notificação externa não pôde ser entregue.")

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_DISCORD_ERRORS


@dataclass(frozen=True, slots=True)
class DiscordMessage:
    content: str


@dataclass(frozen=True, slots=True)
class DiscordHttpResponse:
    status_code: int
    body: bytes


class DiscordHttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> DiscordHttpResponse: ...


class DiscordWebhook(Protocol):
    def send(self, message: DiscordMessage) -> None: ...


class UrllibDiscordHttpTransport:
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> DiscordHttpResponse:
        request = Request(url, headers=headers, data=body, method="POST")
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read(MAX_DISCORD_RESPONSE_BYTES + 1)
            return DiscordHttpResponse(response.status, response_body)


class OfficialDiscordWebhook:
    def __init__(
        self,
        webhook_url: str,
        *,
        transport: DiscordHttpTransport | None = None,
        timeout_seconds: float = DISCORD_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("o timeout do Discord deve ser positivo")
        validate_discord_webhook_url(webhook_url)
        self._webhook_url = webhook_url
        self._transport = transport or UrllibDiscordHttpTransport()
        self._timeout_seconds = timeout_seconds

    def send(self, message: DiscordMessage) -> None:
        if not message.content or len(message.content) > 2000:
            raise ValueError("a mensagem do Discord deve ter entre 1 e 2000 caracteres")
        body = json.dumps(
            {
                "content": message.content,
                "allowed_mentions": {"parse": []},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            response = self._transport.post(
                self._webhook_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Palworld-Manager/0.1",
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except HTTPError as error:
            raise DiscordDeliveryError(_http_error_kind(error.code)) from None
        except TimeoutError:
            raise DiscordDeliveryError(DiscordDeliveryErrorKind.TIMEOUT) from None
        except URLError as error:
            kind = (
                DiscordDeliveryErrorKind.TIMEOUT
                if isinstance(error.reason, TimeoutError)
                else DiscordDeliveryErrorKind.UNAVAILABLE
            )
            raise DiscordDeliveryError(kind) from None
        except OSError:
            raise DiscordDeliveryError(DiscordDeliveryErrorKind.UNAVAILABLE) from None
        except DiscordDeliveryError:
            raise
        except Exception:
            raise DiscordDeliveryError(DiscordDeliveryErrorKind.UNAVAILABLE) from None

        if len(response.body) > MAX_DISCORD_RESPONSE_BYTES:
            raise DiscordDeliveryError(DiscordDeliveryErrorKind.INVALID_RESPONSE)
        if response.status_code not in {200, 204}:
            raise DiscordDeliveryError(_http_error_kind(response.status_code))


class UnconfiguredDiscordWebhook:
    def send(self, message: DiscordMessage) -> None:
        del message
        raise DiscordDeliveryError(DiscordDeliveryErrorKind.NOT_CONFIGURED)


class FakeDiscordWebhook:
    def __init__(self) -> None:
        self.messages: list[DiscordMessage] = []
        self._failures: list[DiscordDeliveryErrorKind] = []

    def queue_failure(self, kind: DiscordDeliveryErrorKind) -> None:
        self._failures.append(kind)

    def send(self, message: DiscordMessage) -> None:
        if self._failures:
            raise DiscordDeliveryError(self._failures.pop(0))
        self.messages.append(message)


def create_discord_webhook(settings: Settings) -> DiscordWebhook:
    if settings.environment is not AppEnvironment.PRODUCTION:
        return FakeDiscordWebhook()
    secret = settings.discord_webhook_url
    if secret is None:
        return UnconfiguredDiscordWebhook()
    return OfficialDiscordWebhook(secret.get_secret_value())


def _http_error_kind(status_code: int) -> DiscordDeliveryErrorKind:
    if status_code == 429:
        return DiscordDeliveryErrorKind.RATE_LIMITED
    if status_code == 408 or status_code >= 500:
        return DiscordDeliveryErrorKind.UNAVAILABLE
    if status_code in {401, 403, 404}:
        return DiscordDeliveryErrorKind.REJECTED
    return DiscordDeliveryErrorKind.INVALID_RESPONSE
