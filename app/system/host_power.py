from enum import StrEnum
from typing import Protocol

from app.config import AppEnvironment, Settings
from app.system.host_control import (
    HostControlRequester,
    HostControlRequestError,
    PrivilegedHostAction,
    request_host_control,
)


class HostPowerAction(StrEnum):
    REBOOT = "REBOOT"
    SHUTDOWN = "SHUTDOWN"


class HostPowerControlError(RuntimeError):
    """O systemd não aceitou a solicitação de energia do host."""


class HostPowerController(Protocol):
    def request(self, action: HostPowerAction) -> None: ...


class SystemdHostPowerController:
    def __init__(
        self,
        *,
        host_control_requester: HostControlRequester = request_host_control,
    ) -> None:
        self._host_control_requester = host_control_requester

    def request(self, action: HostPowerAction) -> None:
        if not isinstance(action, HostPowerAction):
            raise ValueError("ação de energia do host inválida")
        privileged_action = {
            HostPowerAction.REBOOT: PrivilegedHostAction.HOST_REBOOT,
            HostPowerAction.SHUTDOWN: PrivilegedHostAction.HOST_POWEROFF,
        }[action]
        try:
            self._host_control_requester(privileged_action)
        except HostControlRequestError as error:
            raise HostPowerControlError(
                "Não foi possível solicitar a ação de energia do host."
            ) from error


class FakeHostPowerController:
    def __init__(self) -> None:
        self.requests: list[HostPowerAction] = []

    def request(self, action: HostPowerAction) -> None:
        if not isinstance(action, HostPowerAction):
            raise ValueError("ação de energia do host inválida")
        self.requests.append(action)


def create_host_power_controller(settings: Settings) -> HostPowerController:
    if settings.environment is AppEnvironment.PRODUCTION:
        return SystemdHostPowerController()
    return FakeHostPowerController()
