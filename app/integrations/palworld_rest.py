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
MAX_PLAYERS_RESPONSE_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = MAX_PLAYERS_RESPONSE_BYTES


class RestApiState(StrEnum):
    AVAILABLE = "available"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    FAILURE = "failure"


class PalworldRestErrorKind(StrEnum):
    SERVER_OFFLINE = "server_offline"
    UNAUTHORIZED = "unauthorized"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    FAILURE = "failure"


ERROR_MESSAGES = {
    PalworldRestErrorKind.SERVER_OFFLINE: "O servidor Palworld parece estar offline.",
    PalworldRestErrorKind.UNAUTHORIZED: "A autenticação da API do Palworld foi rejeitada.",
    PalworldRestErrorKind.TIMEOUT: "A API do Palworld não respondeu dentro do tempo limite.",
    PalworldRestErrorKind.UNAVAILABLE: "A API do Palworld está indisponível no momento.",
    PalworldRestErrorKind.INVALID_RESPONSE: "A API do Palworld retornou uma resposta inválida.",
    PalworldRestErrorKind.FAILURE: "Ocorreu uma falha inesperada ao acessar a API do Palworld.",
}


@dataclass(frozen=True, slots=True)
class PalworldServerInfo:
    version: str
    servername: str
    description: str
    worldguid: str


@dataclass(frozen=True, slots=True)
class PalworldPlayer:
    name: str
    account_name: str | None
    player_id: str
    user_id: str
    ip: str
    ping: float
    location_x: float
    location_y: float
    level: int
    building_count: int | None


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


class PalworldRestTransport(HttpTransport, Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout_seconds: float) -> HttpResponse:
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return HttpResponse(status_code=response.status, body=body)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url, headers=headers, data=body, method="POST")
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            return HttpResponse(status_code=response.status, body=response_body)


class PalworldRestOperationError(RuntimeError):
    """Uma operação administrativa oficial não pôde ser confirmada."""


class PalworldRestError(PalworldRestOperationError):
    def __init__(self, kind: PalworldRestErrorKind) -> None:
        self.kind = kind
        super().__init__(ERROR_MESSAGES[kind])

    @property
    def public_message(self) -> str:
        return ERROR_MESSAGES[self.kind]


class PalworldRestClient(Protocol):
    def server_info(self) -> PalworldServerInfo: ...

    def players(self) -> tuple[PalworldPlayer, ...]: ...

    def announce(self, message: str) -> None: ...


class OfficialPalworldRestClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: PalworldRestTransport | None = None,
        timeout_seconds: float = REST_TIMEOUT_SECONDS,
    ) -> None:
        _validate_credentials(username, password)
        if timeout_seconds <= 0:
            raise ValueError("o timeout da REST API deve ser positivo")
        self._base_url = base_url.rstrip("/")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._headers = {"Accept": "application/json", "Authorization": f"Basic {encoded}"}
        self._transport = transport or UrllibHttpTransport()
        self._timeout_seconds = timeout_seconds

    def server_info(self) -> PalworldServerInfo:
        response = self._get("info", MAX_INFO_RESPONSE_BYTES)
        try:
            payload = json.loads(response.body)
            return PalworldServerInfo(
                version=_required_string(payload, "version"),
                servername=_required_string(payload, "servername"),
                description=_required_string(payload, "description"),
                worldguid=_required_string(payload, "worldguid"),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise PalworldRestError(PalworldRestErrorKind.INVALID_RESPONSE) from error

    def players(self) -> tuple[PalworldPlayer, ...]:
        response = self._get("players", MAX_PLAYERS_RESPONSE_BYTES)
        try:
            payload = json.loads(response.body)
            raw_players = _required_list(payload, "players")
            return tuple(_parse_player(player) for player in raw_players)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise PalworldRestError(PalworldRestErrorKind.INVALID_RESPONSE) from error

    def announce(self, message: str) -> None:
        if not message.strip():
            raise ValueError("a mensagem do anúncio é obrigatória")
        headers = {**self._headers, "Content-Type": "application/json"}
        body = json.dumps({"message": message}, ensure_ascii=False).encode()
        response = self._request(
            "POST",
            "announce",
            headers=headers,
            body=body,
        )
        self._validate_response(response, MAX_INFO_RESPONSE_BYTES)

    def _get(self, endpoint: str, max_response_bytes: int) -> HttpResponse:
        response = self._request("GET", endpoint, headers=self._headers)
        self._validate_response(response, max_response_bytes)
        return response

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        url = f"{self._base_url}/{endpoint}"
        try:
            if method == "GET":
                return self._transport.get(
                    url,
                    headers=headers,
                    timeout_seconds=self._timeout_seconds,
                )
            assert body is not None
            return self._transport.post(
                url,
                headers=headers,
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except HTTPError as error:
            raise PalworldRestError(_http_error_kind(error.code)) from error
        except TimeoutError as error:
            raise PalworldRestError(PalworldRestErrorKind.TIMEOUT) from error
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                kind = PalworldRestErrorKind.TIMEOUT
            elif isinstance(error.reason, ConnectionRefusedError):
                kind = PalworldRestErrorKind.SERVER_OFFLINE
            else:
                kind = PalworldRestErrorKind.UNAVAILABLE
            raise PalworldRestError(kind) from error
        except ConnectionRefusedError as error:
            raise PalworldRestError(PalworldRestErrorKind.SERVER_OFFLINE) from error
        except OSError as error:
            raise PalworldRestError(PalworldRestErrorKind.UNAVAILABLE) from error
        except Exception as error:
            raise PalworldRestError(PalworldRestErrorKind.FAILURE) from error

    @staticmethod
    def _validate_response(response: HttpResponse, max_response_bytes: int) -> None:
        if response.status_code != 200:
            raise PalworldRestError(_http_error_kind(response.status_code))
        if len(response.body) > max_response_bytes:
            raise PalworldRestError(PalworldRestErrorKind.INVALID_RESPONSE)


class FakePalworldRestClient:
    def __init__(
        self,
        *,
        info: PalworldServerInfo | None = None,
        players: tuple[PalworldPlayer, ...] | None = None,
    ) -> None:
        self.info = info or PalworldServerInfo(
            version="v0.0.0-fake",
            servername="Servidor Palworld simulado",
            description="Ambiente local sem acesso ao servidor real.",
            worldguid="00000000000000000000000000000000",
        )
        self.online_players = players or ()
        self.announcements: list[str] = []
        self.player_queries = 0
        self.error: PalworldRestErrorKind | None = None

    def server_info(self) -> PalworldServerInfo:
        self._raise_configured_error()
        return self.info

    def players(self) -> tuple[PalworldPlayer, ...]:
        self.player_queries += 1
        self._raise_configured_error()
        return self.online_players

    def announce(self, message: str) -> None:
        if not message.strip():
            raise ValueError("a mensagem do anúncio é obrigatória")
        self._raise_configured_error()
        self.announcements.append(message)

    def set_error(self, error: PalworldRestErrorKind | None) -> None:
        self.error = error

    def _raise_configured_error(self) -> None:
        if self.error is not None:
            raise PalworldRestError(self.error)


class PalworldShutdownCommunicator(Protocol):
    def online_player_count(self) -> int: ...

    def announce(self, message: str) -> None: ...


class OfficialPalworldShutdownCommunicator:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: PalworldRestTransport | None = None,
        timeout_seconds: float = REST_TIMEOUT_SECONDS,
    ) -> None:
        self._client = OfficialPalworldRestClient(
            base_url,
            username,
            password,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    def online_player_count(self) -> int:
        return len(self._client.players())

    def announce(self, message: str) -> None:
        self._client.announce(message)


class FakePalworldShutdownCommunicator:
    def __init__(self, online_players: int = 0) -> None:
        self.online_players = online_players
        self.announcements: list[str] = []

    def online_player_count(self) -> int:
        return self.online_players

    def announce(self, message: str) -> None:
        self.announcements.append(message)


class PalworldRestHealthProbe(Protocol):
    def probe(self) -> RestApiProbeResult: ...


class OfficialPalworldRestHealthProbe:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: PalworldRestTransport | None = None,
        timeout_seconds: float = REST_TIMEOUT_SECONDS,
    ) -> None:
        self._client = OfficialPalworldRestClient(
            base_url,
            username,
            password,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    def probe(self) -> RestApiProbeResult:
        try:
            return RestApiProbeResult(RestApiState.AVAILABLE, self._client.server_info())
        except PalworldRestError as error:
            states = {
                PalworldRestErrorKind.UNAUTHORIZED: RestApiState.UNAUTHORIZED,
                PalworldRestErrorKind.SERVER_OFFLINE: RestApiState.UNAVAILABLE,
                PalworldRestErrorKind.TIMEOUT: RestApiState.UNAVAILABLE,
                PalworldRestErrorKind.UNAVAILABLE: RestApiState.UNAVAILABLE,
                PalworldRestErrorKind.INVALID_RESPONSE: RestApiState.INVALID_RESPONSE,
                PalworldRestErrorKind.FAILURE: RestApiState.FAILURE,
            }
            return RestApiProbeResult(states[error.kind])


class FakePalworldRestHealthProbe:
    def __init__(self, state: RestApiState = RestApiState.UNAVAILABLE) -> None:
        self._state = state

    def probe(self) -> RestApiProbeResult:
        return RestApiProbeResult(self._state)

    def set_state(self, state: RestApiState) -> None:
        self._state = state


def _validate_credentials(username: str, password: str) -> None:
    if not username.strip() or not password.strip():
        raise ValueError("credenciais REST do Palworld são obrigatórias")
    if ":" in username or "\r" in username or "\n" in username:
        raise ValueError("username REST do Palworld possui formato inválido")
    if "\r" in password or "\n" in password:
        raise ValueError("password REST do Palworld possui formato inválido")


def _http_error_kind(status_code: int) -> PalworldRestErrorKind:
    if status_code == 401:
        return PalworldRestErrorKind.UNAUTHORIZED
    if status_code >= 500:
        return PalworldRestErrorKind.UNAVAILABLE
    return PalworldRestErrorKind.INVALID_RESPONSE


def _required_string(payload: object, field: str) -> str:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if not isinstance(value, str):
        raise TypeError(f"o campo {field} deve ser texto")
    return value


def _optional_string(payload: object, field: str) -> str | None:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"o campo {field} deve ser texto")
    return value


def _required_number(payload: object, field: str) -> float:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"o campo {field} deve ser numérico")
    return float(value)


def _required_integer(payload: object, field: str) -> int:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if type(value) is not int:
        raise TypeError(f"o campo {field} deve ser inteiro")
    return value


def _optional_integer(payload: object, field: str) -> int | None:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if value is not None and type(value) is not int:
        raise TypeError(f"o campo {field} deve ser inteiro")
    return value


def _required_list(payload: object, field: str) -> list[object]:
    if not isinstance(payload, dict):
        raise TypeError("a resposta deve ser um objeto")
    value = payload.get(field)
    if not isinstance(value, list):
        raise TypeError(f"o campo {field} deve ser uma lista")
    return value


def _parse_player(payload: object) -> PalworldPlayer:
    return PalworldPlayer(
        name=_required_string(payload, "name"),
        account_name=_optional_string(payload, "accountName"),
        player_id=_required_string(payload, "playerId"),
        user_id=_required_string(payload, "userId"),
        ip=_required_string(payload, "ip"),
        ping=_required_number(payload, "ping"),
        location_x=_required_number(payload, "location_x"),
        location_y=_required_number(payload, "location_y"),
        level=_required_integer(payload, "level"),
        building_count=_optional_integer(payload, "building_count"),
    )


def _production_credentials(settings: Settings) -> tuple[str, str]:
    username = settings.palworld_rest_username
    password = settings.palworld_rest_password
    if username is None or password is None:
        raise ValueError("credenciais REST do Palworld são obrigatórias em production")
    return username.get_secret_value(), password.get_secret_value()


def create_palworld_rest_client(settings: Settings) -> PalworldRestClient:
    if settings.environment is not AppEnvironment.PRODUCTION:
        return FakePalworldRestClient()
    username, password = _production_credentials(settings)
    return OfficialPalworldRestClient(
        str(settings.palworld_rest_base_url),
        username,
        password,
    )


def create_palworld_rest_health_probe(settings: Settings) -> PalworldRestHealthProbe:
    if settings.environment is not AppEnvironment.PRODUCTION:
        return FakePalworldRestHealthProbe()
    username, password = _production_credentials(settings)
    return OfficialPalworldRestHealthProbe(
        str(settings.palworld_rest_base_url),
        username,
        password,
    )


def create_palworld_shutdown_communicator(settings: Settings) -> PalworldShutdownCommunicator:
    if settings.environment is not AppEnvironment.PRODUCTION:
        return FakePalworldShutdownCommunicator()
    username, password = _production_credentials(settings)
    return OfficialPalworldShutdownCommunicator(
        str(settings.palworld_rest_base_url),
        username,
        password,
    )
