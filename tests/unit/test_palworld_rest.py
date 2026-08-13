import json
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from app.config import AppEnvironment, Settings
from app.integrations.palworld_rest import (
    FakePalworldRestHealthProbe,
    HttpResponse,
    OfficialPalworldRestHealthProbe,
    RestApiState,
    create_palworld_rest_health_probe,
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

    def get(self, url: str, *, headers: dict[str, str], timeout_seconds: float) -> HttpResponse:
        self.url = url
        self.headers = headers
        self.timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


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
