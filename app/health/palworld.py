from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.config import Settings
from app.integrations.palworld_rest import (
    PalworldRestHealthProbe,
    RestApiProbeResult,
    RestApiState,
    create_palworld_rest_health_probe,
)
from app.system.palworld_process import (
    PalworldProcessProbe,
    PalworldProcessQueryError,
    create_palworld_process_probe,
)
from app.system.palworld_service import (
    PalworldService,
    PalworldServiceQueryError,
    PalworldServiceStatus,
)


class PalworldHealthState(StrEnum):
    ONLINE = "ONLINE"
    STARTING = "INICIANDO"
    DEGRADED = "DEGRADADO"
    OFFLINE = "OFFLINE"
    FAILURE = "FALHA"


@dataclass(frozen=True, slots=True)
class PalworldHealthSnapshot:
    state: PalworldHealthState
    service_state: str | None
    process_running: bool | None
    rest_api_state: RestApiState


class PalworldHealthChecker(Protocol):
    def check(self) -> PalworldHealthSnapshot: ...


class PalworldHealthCheck:
    def __init__(
        self,
        service: PalworldService,
        process: PalworldProcessProbe,
        rest_api: PalworldRestHealthProbe,
    ) -> None:
        self._service = service
        self._process = process
        self._rest_api = rest_api

    def check(self) -> PalworldHealthSnapshot:
        try:
            service = self._service.get_status()
        except PalworldServiceQueryError:
            return PalworldHealthSnapshot(
                state=PalworldHealthState.FAILURE,
                service_state=None,
                process_running=None,
                rest_api_state=RestApiState.FAILURE,
            )

        try:
            process_running = self._process.is_running()
        except PalworldProcessQueryError:
            return PalworldHealthSnapshot(
                state=PalworldHealthState.FAILURE,
                service_state=service.source_state,
                process_running=None,
                rest_api_state=RestApiState.FAILURE,
            )

        rest_api = self._rest_api.probe()
        return evaluate_palworld_health(service, process_running, rest_api)


def evaluate_palworld_health(
    service: PalworldServiceStatus,
    process_running: bool,
    rest_api: RestApiProbeResult,
) -> PalworldHealthSnapshot:
    source_state = service.source_state
    rest_available = rest_api.state is RestApiState.AVAILABLE

    if source_state == "failed":
        state = PalworldHealthState.FAILURE
    elif source_state == "activating":
        state = PalworldHealthState.STARTING
    elif source_state == "inactive":
        state = (
            PalworldHealthState.OFFLINE
            if not process_running and not rest_available
            else PalworldHealthState.FAILURE
        )
    elif source_state == "deactivating":
        state = (
            PalworldHealthState.DEGRADED
            if process_running or rest_available
            else PalworldHealthState.OFFLINE
        )
    elif service.active:
        if not process_running:
            state = PalworldHealthState.FAILURE
        elif rest_available:
            state = PalworldHealthState.ONLINE
        else:
            state = PalworldHealthState.DEGRADED
    elif process_running and rest_available:
        state = PalworldHealthState.DEGRADED
    else:
        state = PalworldHealthState.FAILURE

    return PalworldHealthSnapshot(
        state=state,
        service_state=source_state,
        process_running=process_running,
        rest_api_state=rest_api.state,
    )


def create_palworld_health_check(
    settings: Settings,
    service: PalworldService,
) -> PalworldHealthChecker:
    return PalworldHealthCheck(
        service,
        create_palworld_process_probe(settings),
        create_palworld_rest_health_probe(settings),
    )
