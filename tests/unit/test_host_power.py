from typing import cast

import pytest

from app.system.host_control import HostControlRequestError, PrivilegedHostAction
from app.system.host_power import (
    HostPowerAction,
    HostPowerControlError,
    SystemdHostPowerController,
)


class RecordingRequester:
    def __init__(self) -> None:
        self.actions: list[PrivilegedHostAction] = []

    def __call__(self, action: PrivilegedHostAction) -> None:
        self.actions.append(action)


def test_systemd_host_power_requests_only_fixed_actions() -> None:
    requester = RecordingRequester()

    controller = SystemdHostPowerController(host_control_requester=requester)
    controller.request(HostPowerAction.REBOOT)
    controller.request(HostPowerAction.SHUTDOWN)

    assert requester.actions == [
        PrivilegedHostAction.HOST_REBOOT,
        PrivilegedHostAction.HOST_POWEROFF,
    ]


def test_systemd_host_power_rejects_arbitrary_action_without_running_command() -> None:
    requester = RecordingRequester()
    controller = SystemdHostPowerController(host_control_requester=requester)

    with pytest.raises(ValueError):
        controller.request(cast(HostPowerAction, "reboot; touch /tmp/nao-executar"))

    assert requester.actions == []


def test_systemd_host_power_never_copies_command_output_to_error() -> None:
    external_detail = "external-sensitive-detail"

    def requester(action: PrivilegedHostAction) -> None:
        del action
        raise HostControlRequestError(external_detail)

    controller = SystemdHostPowerController(host_control_requester=requester)

    with pytest.raises(HostPowerControlError) as captured:
        controller.request(HostPowerAction.REBOOT)

    assert external_detail not in str(captured.value)
