import json
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from app.config import AppEnvironment, Settings
from app.integrations.palworld_rest import (
    FakePalworldRestClient,
    FakePalworldRestHealthProbe,
    FakePalworldShutdownCommunicator,
    HttpResponse,
    OfficialPalworldRestClient,
    OfficialPalworldRestHealthProbe,
    OfficialPalworldShutdownCommunicator,
    PalworldPlayer,
    PalworldRestError,
    PalworldRestErrorKind,
    PalworldRestOperationError,
    RestApiState,
    create_palworld_rest_client,
    create_palworld_rest_health_probe,
    create_palworld_shutdown_communicator,
)


class RecordingTransport:
    def __init__(
        self,
        response: HttpResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.url: str | None = None
        self.headers: dict[str, str] | None = None
        self.timeout_seconds: float | None = None
        self.body: bytes | None = None

    def get(self, url: str, *, headers: dict[str, str], timeout_seconds: float) -> HttpResponse:
        self.url = url
        self.headers = headers
        self.timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.body = body
        return self.get(url, headers=headers, timeout_seconds=timeout_seconds)


def valid_response() -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body=json.dumps(
            {
                "version": "v0.6.0.0",
                "servername": "Servidor de teste",
                "description": "Ambiente simulado",
                "worldguid": "00000000-0000-0000-0000-000000000000",
            }
        ).encode(),
    )


def valid_players_response() -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body=json.dumps(
            {
                "players": [
                    {
                        "name": "PalUser",
                        "accountName": "paluser",
                        "playerId": "AFAFD830000000000000000000000000",
                        "userId": "steam_00000000000000000",
                        "ip": "127.0.0.1",
                        "ping": 3.14,
                        "location_x": 123.45,
                        "location_y": 67.89,
                        "level": 12,
                        "building_count": 119,
                    }
                ]
            }
        ).encode(),
    )


def test_official_probe_calls_info_with_basic_auth_and_timeout() -> None:
    transport = RecordingTransport(valid_response())
    probe = OfficialPalworldRestHealthProbe(
        "http://127.0.0.1:8212/v1/api/",
        "usuario-ficticio",
        "senha-ficticia",
        transport=transport,
    )

    result = probe.probe()

    assert result.state is RestApiState.AVAILABLE
    assert result.info is not None
    assert result.info.servername == "Servidor de teste"
    assert transport.url == "http://127.0.0.1:8212/v1/api/info"
    assert transport.headers is not None
    assert transport.headers["Accept"] == "application/json"
    assert transport.headers["Authorization"].startswith("Basic ")
    assert "usuario-ficticio" not in transport.headers["Authorization"]
    assert "senha-ficticia" not in transport.headers["Authorization"]
    assert transport.timeout_seconds == 5.0


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", "senha-ficticia"),
        ("usuario:invalido", "senha-ficticia"),
        ("usuario-ficticio", ""),
        ("usuario-ficticio", "senha\ninvalida"),
    ],
)
def test_official_probe_rejects_invalid_credentials(username: str, password: str) -> None:
    with pytest.raises(ValueError, match=r"obrigatórias|formato inválido"):
        OfficialPalworldRestHealthProbe(
            "http://127.0.0.1:8212/v1/api",
            username,
            password,
            transport=RecordingTransport(valid_response()),
        )


@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        (HTTPError("url", 401, "unauthorized", Message(), None), RestApiState.UNAUTHORIZED),
        (HTTPError("url", 503, "unavailable", Message(), None), RestApiState.UNAVAILABLE),
        (URLError("offline"), RestApiState.UNAVAILABLE),
        (TimeoutError(), RestApiState.UNAVAILABLE),
        (RuntimeError("unexpected"), RestApiState.FAILURE),
    ],
)
def test_official_probe_classifies_transport_failures(
    error: Exception,
    expected_state: RestApiState,
) -> None:
    probe = OfficialPalworldRestHealthProbe(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=RecordingTransport(error=error),
    )

    assert probe.probe().state is expected_state


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(status_code=400, body=b"{}"),
        HttpResponse(status_code=200, body=b"not-json"),
        HttpResponse(status_code=200, body=b"{}"),
        HttpResponse(status_code=200, body=b'{"version": 123}'),
    ],
)
def test_official_probe_rejects_invalid_responses(response: HttpResponse) -> None:
    probe = OfficialPalworldRestHealthProbe(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=RecordingTransport(response),
    )

    assert probe.probe().state is RestApiState.INVALID_RESPONSE


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_non_production_environments_use_complete_rest_fake(
    environment: AppEnvironment,
) -> None:
    probe = create_palworld_rest_health_probe(Settings(environment=environment))

    assert isinstance(probe, FakePalworldRestHealthProbe)
    assert probe.probe().state is RestApiState.UNAVAILABLE


def test_shutdown_communicator_uses_official_players_and_announce_endpoints() -> None:
    response = valid_players_response()
    payload = json.loads(response.body)
    payload["players"].append(payload["players"][0])
    transport = RecordingTransport(HttpResponse(200, json.dumps(payload).encode()))
    communicator = OfficialPalworldShutdownCommunicator(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=transport,
    )

    assert communicator.online_player_count() == 2
    assert transport.url == "http://127.0.0.1:8212/v1/api/players"

    communicator.announce("Aviso de teste")
    assert transport.url == "http://127.0.0.1:8212/v1/api/announce"
    assert transport.body == b'{"message": "Aviso de teste"}'
    assert transport.headers is not None
    assert transport.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(401, b"{}"),
        HttpResponse(200, b"not-json"),
        HttpResponse(200, b'{"players": {}}'),
    ],
)
def test_shutdown_communicator_rejects_untrusted_player_responses(
    response: HttpResponse,
) -> None:
    communicator = OfficialPalworldShutdownCommunicator(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=RecordingTransport(response),
    )

    with pytest.raises(PalworldRestOperationError):
        communicator.online_player_count()


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_non_production_shutdown_communication_is_fully_fake(
    environment: AppEnvironment,
) -> None:
    communicator = create_palworld_shutdown_communicator(Settings(environment=environment))

    assert isinstance(communicator, FakePalworldShutdownCommunicator)


def test_official_client_parses_typed_players_from_official_fields() -> None:
    transport = RecordingTransport(valid_players_response())
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=transport,
    )

    players = client.players()

    assert players == (
        PalworldPlayer(
            name="PalUser",
            account_name="paluser",
            player_id="AFAFD830000000000000000000000000",
            user_id="steam_00000000000000000",
            ip="127.0.0.1",
            ping=3.14,
            location_x=123.45,
            location_y=67.89,
            level=12,
            building_count=119,
        ),
    )
    assert transport.url == "http://127.0.0.1:8212/v1/api/players"
    assert transport.timeout_seconds == 5.0


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (
            HTTPError("url", 401, "credencial-super-secreta", Message(), None),
            PalworldRestErrorKind.UNAUTHORIZED,
        ),
        (URLError(ConnectionRefusedError()), PalworldRestErrorKind.SERVER_OFFLINE),
        (URLError(TimeoutError()), PalworldRestErrorKind.TIMEOUT),
        (URLError("network"), PalworldRestErrorKind.UNAVAILABLE),
        (RuntimeError("senha-super-secreta"), PalworldRestErrorKind.FAILURE),
    ],
)
def test_official_client_classifies_failures_without_exposing_details(
    error: Exception,
    expected_kind: PalworldRestErrorKind,
) -> None:
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=RecordingTransport(error=error),
    )

    with pytest.raises(PalworldRestError) as raised:
        client.players()

    assert raised.value.kind is expected_kind
    assert "super-secreta" not in raised.value.public_message
    assert "usuario-ficticio" not in raised.value.public_message


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(401, b"{}"),
        HttpResponse(503, b"{}"),
        HttpResponse(200, b"not-json"),
        HttpResponse(200, b'{"players": [{}]}'),
        HttpResponse(200, b'{"players": [{"name": 1}]}'),
    ],
)
def test_official_client_rejects_status_and_invalid_player_payloads(
    response: HttpResponse,
) -> None:
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=RecordingTransport(response),
    )

    with pytest.raises(PalworldRestError):
        client.players()


def test_official_client_sends_exact_free_text_announcement() -> None:
    transport = RecordingTransport(HttpResponse(200, b"{}"))
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=transport,
    )

    client.announce("Olá, jogadores!\nReinício em breve.")

    assert transport.url == "http://127.0.0.1:8212/v1/api/announce"
    assert json.loads(transport.body or b"") == {"message": "Olá, jogadores!\nReinício em breve."}
    assert transport.headers is not None
    assert transport.headers["Content-Type"] == "application/json"


def test_official_client_requests_world_save_with_confirmed_endpoint() -> None:
    transport = RecordingTransport(HttpResponse(200, b"{}"))
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=transport,
    )

    client.save_world()

    assert transport.url == "http://127.0.0.1:8212/v1/api/save"
    assert transport.body == b""


def test_official_client_uses_only_documented_player_action_contracts() -> None:
    transport = RecordingTransport(HttpResponse(200, b"{}"))
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=transport,
    )

    client.kick("steam_00000000000000000")
    assert transport.url == "http://127.0.0.1:8212/v1/api/kick"
    assert json.loads(transport.body or b"") == {"userid": "steam_00000000000000000"}

    client.ban("steam_00000000000000000", "Conduta inadequada")
    assert transport.url == "http://127.0.0.1:8212/v1/api/ban"
    assert json.loads(transport.body or b"") == {
        "userid": "steam_00000000000000000",
        "message": "Conduta inadequada",
    }

    client.unban("steam_00000000000000000")
    assert transport.url == "http://127.0.0.1:8212/v1/api/unban"
    assert json.loads(transport.body or b"") == {"userid": "steam_00000000000000000"}
    assert transport.headers is not None
    assert transport.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("user_id", ["", "   ", "steam\ninvalid"])
def test_official_client_rejects_invalid_player_user_id(user_id: str) -> None:
    client = OfficialPalworldRestClient(
        "http://127.0.0.1:8212/v1/api",
        "usuario-ficticio",
        "senha-ficticia",
        transport=RecordingTransport(HttpResponse(200, b"{}")),
    )

    with pytest.raises(ValueError, match="User ID"):
        client.kick(user_id)


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_non_production_environments_use_complete_administrative_fake(
    environment: AppEnvironment,
) -> None:
    client = create_palworld_rest_client(Settings(environment=environment))

    assert isinstance(client, FakePalworldRestClient)
    assert client.server_info().servername == "Servidor Palworld simulado"
    assert client.players() == ()
    client.announce("Mensagem simulada")
    assert client.announcements == ["Mensagem simulada"]
    client.kick("steam-kick")
    client.ban("steam-ban", "Motivo livre")
    client.unban("steam-unban")
    assert client.kicks == [("steam-kick", None)]
    assert client.bans == [("steam-ban", "Motivo livre")]
    assert client.unbans == ["steam-unban"]
