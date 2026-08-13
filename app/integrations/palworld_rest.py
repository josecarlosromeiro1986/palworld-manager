import base64
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import AppEnvironment, Settings

REST_TIMEOUT_SECONDS = 5.0
MAX_INFO_RESPONSE_BYTES = 64 * 1024


class RestApiState(StrEnum):
    AVAILABLE = "available"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class PalworldServerInfo:
    version: str
    servername: str
    description: str
    worldguid: str


@dataclass(frozen=True, slots=True)
class RestApiProbeResult:
    state: RestApiState
    info: PalworldServerInfo | None = None


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout_seconds: float) -> HttpResponse: ...


class UrllibHttpTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout_seconds: float) -> HttpResponse:
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(MAX_INFO_RESPONSE_BYTES + 1)
            return HttpResponse(status_code=response.status, body=body)


class PalworldRestHealthProbe(Protocol):
    def probe(self) -> RestApiProbeResult: ...


class OfficialPalworldRestHealthProbe:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: HttpTransport | None = None,
        timeout_seconds: float = REST_TIMEOUT_SECONDS,
    ) -> None:
        if not username.strip() or not password.strip():
            raise ValueError("credenciais REST do Palworld são obrigatórias")
        if ":" in username or "\r" in username or "\n" in username:
            raise ValueError("username REST do Palworld possui formato inválido")
        if "\r" in password or "\n" in password:
            raise ValueError("password REST do Palworld possui formato inválido")
        if timeout_seconds <= 0:
            raise ValueError("o timeout da REST API deve ser positivo")
        self._info_url = f"{base_url.rstrip('/')}/info"
        encoded_credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {encoded_credentials}",
        }
        self._transport = transport or UrllibHttpTransport()
        self._timeout_seconds = timeout_seconds

    def probe(self) -> RestApiProbeResult:
        try:
            response = self._transport.get(
                self._info_url,
                headers=self._headers,
                timeout_seconds=self._timeout_seconds,
            )
        except HTTPError as error:
            if error.code == 401:
                return RestApiProbeResult(RestApiState.UNAUTHORIZED)
            if error.code >= 500:
                return RestApiProbeResult(RestApiState.UNAVAILABLE)
            return RestApiProbeResult(RestApiState.INVALID_RESPONSE)
        except (TimeoutError, URLError, OSError):
            return RestApiProbeResult(RestApiState.UNAVAILABLE)
        except Exception:
            return RestApiProbeResult(RestApiState.FAILURE)

        if response.status_code == 401:
            return RestApiProbeResult(RestApiState.UNAUTHORIZED)
        if response.status_code >= 500:
            return RestApiProbeResult(RestApiState.UNAVAILABLE)
        if response.status_code != 200 or len(response.body) > MAX_INFO_RESPONSE_BYTES:
            return RestApiProbeResult(RestApiState.INVALID_RESPONSE)

        try:
            payload = json.loads(response.body)
            info = PalworldServerInfo(
                version=_required_string(payload, "version"),
                servername=_required_string(payload, "servername"),
                description=_required_string(payload, "description"),
                worldguid=_required_string(payload, "worldguid"),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return RestApiProbeResult(RestApiState.INVALID_RESPONSE)
        return RestApiProbeResult(RestApiState.AVAILABLE, info)


class FakePalworldRestHealthProbe:
    def __init__(self, state: RestApiState = RestApiState.UNAVAILABLE) -> None:
        self._state = state

    def probe(self) -> RestApiProbeResult:
        return RestApiProbeResult(self._state)

    def set_state(self, state: RestApiState) -> None:
        self._state = state


def _required_string(payload: object, field: str) -> str:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if not isinstance(value, str):
        raise TypeError(f"o campo {field} deve ser texto")
    return value


def create_palworld_rest_health_probe(settings: Settings) -> PalworldRestHealthProbe:
    if settings.environment is not AppEnvironment.PRODUCTION:
        return FakePalworldRestHealthProbe()

    username = settings.palworld_rest_username
    password = settings.palworld_rest_password
    if username is None or password is None:
        raise ValueError("credenciais REST do Palworld são obrigatórias em production")
    return OfficialPalworldRestHealthProbe(
        str(settings.palworld_rest_base_url),
        username.get_secret_value(),
        password.get_secret_value(),
    )
