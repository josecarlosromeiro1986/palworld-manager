from dataclasses import dataclass

import pytest

from app.health.palworld import (
    PalworldHealthCheck,
    PalworldHealthState,
    evaluate_palworld_health,
)
from app.integrations.palworld_rest import RestApiProbeResult, RestApiState
from app.system.palworld_process import PalworldProcessQueryError
from app.system.palworld_service import PalworldServiceQueryError, PalworldServiceStatus


@pytest.mark.parametrize(
    ("source_state", "process_running", "rest_state", "expected_state"),
    [
        ("active", True, RestApiState.AVAILABLE, PalworldHealthState.ONLINE),
        ("activating", False, RestApiState.UNAVAILABLE, PalworldHealthState.STARTING),
        ("active", True, RestApiState.UNAVAILABLE, PalworldHealthState.DEGRADED),
        ("active", True, RestApiState.UNAUTHORIZED, PalworldHealthState.DEGRADED),
        ("inactive", False, RestApiState.UNAVAILABLE, PalworldHealthState.OFFLINE),
        ("failed", False, RestApiState.UNAVAILABLE, PalworldHealthState.FAILURE),
        ("active", False, RestApiState.AVAILABLE, PalworldHealthState.FAILURE),
        ("inactive", True, RestApiState.AVAILABLE, PalworldHealthState.FAILURE),
        ("deactivating", True, RestApiState.UNAVAILABLE, PalworldHealthState.DEGRADED),
        ("deactivating", False, RestApiState.UNAVAILABLE, PalworldHealthState.OFFLINE),
    ],
)
def test_health_state_matrix(
    source_state: str,
    process_running: bool,
    rest_state: RestApiState,
    expected_state: PalworldHealthState,
) -> None:
    snapshot = evaluate_palworld_health(
        PalworldServiceStatus(active=source_state == "active", source_state=source_state),
        process_running,
        RestApiProbeResult(rest_state),
    )

    assert snapshot.state is expected_state


@dataclass
class StubService:
    error: bool = False

    def get_status(self) -> PalworldServiceStatus:
        if self.error:
            raise PalworldServiceQueryError("falha simulada")
        return PalworldServiceStatus(active=True, source_state="active")


@dataclass
class StubProcess:
    error: bool = False

    def is_running(self) -> bool:
        if self.error:
            raise PalworldProcessQueryError("falha simulada")
        return True


class AvailableRest:
    def probe(self) -> RestApiProbeResult:
        return RestApiProbeResult(RestApiState.AVAILABLE)


@pytest.mark.parametrize(
    ("service", "process"),
    [(StubService(error=True), StubProcess()), (StubService(), StubProcess(error=True))],
)
def test_component_query_errors_result_in_failure(
    service: StubService,
    process: StubProcess,
) -> None:
    health_check = PalworldHealthCheck(service, process, AvailableRest())

    snapshot = health_check.check()

    assert snapshot.state is PalworldHealthState.FAILURE
    assert snapshot.rest_api_state is RestApiState.FAILURE
