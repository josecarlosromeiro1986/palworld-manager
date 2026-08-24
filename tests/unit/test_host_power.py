import subprocess
from collections.abc import Sequence
from typing import cast

import pytest

from app.system.host_power import (
    HostPowerAction,
    HostPowerControlError,
    SystemdHostPowerController,
)
from app.system.palworld_service import SUDO_PATH, SYSTEMCTL_PATH


def test_systemd_host_power_uses_only_fixed_non_blocking_commands() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        assert timeout_seconds == 15.0
        normalized = tuple(command)
        calls.append(normalized)
        return subprocess.CompletedProcess(normalized, 0, stdout="", stderr="")

    controller = SystemdHostPowerController(runner=runner)
    controller.request(HostPowerAction.REBOOT)
    controller.request(HostPowerAction.SHUTDOWN)

    assert calls == [
        (SUDO_PATH, "--non-interactive", SYSTEMCTL_PATH, "--no-block", "reboot"),
        (SUDO_PATH, "--non-interactive", SYSTEMCTL_PATH, "--no-block", "poweroff"),
    ]


def test_systemd_host_power_rejects_arbitrary_action_without_running_command() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    controller = SystemdHostPowerController(runner=runner)

    with pytest.raises(ValueError):
        controller.request(cast(HostPowerAction, "reboot; touch /tmp/nao-executar"))

    assert calls == []


def test_systemd_host_power_never_copies_command_output_to_error() -> None:
    external_detail = "external-sensitive-detail"

    def runner(
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=external_detail,
            stderr=external_detail,
        )

    controller = SystemdHostPowerController(runner=runner)

    with pytest.raises(HostPowerControlError) as captured:
        controller.request(HostPowerAction.REBOOT)

    assert external_detail not in str(captured.value)
