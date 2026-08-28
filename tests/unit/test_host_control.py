from typing import cast

import pytest

from app.system.host_control import (
    PrivilegedHostAction,
    host_control_command,
    host_control_unit,
)


def test_host_control_maps_every_closed_action_to_one_exact_unit() -> None:
    units = {host_control_unit(action) for action in PrivilegedHostAction}

    assert units == {
        "palworld-manager-host-control@palworld-start.service",
        "palworld-manager-host-control@palworld-stop.service",
        "palworld-manager-host-control@palworld-restart.service",
        "palworld-manager-host-control@palworld-sigterm.service",
        "palworld-manager-host-control@palworld-sigkill.service",
        "palworld-manager-host-control@host-reboot.service",
        "palworld-manager-host-control@host-poweroff.service",
    }
    for action in PrivilegedHostAction:
        assert host_control_command(action) == (
            "/usr/bin/systemctl",
            "--no-ask-password",
            "start",
            host_control_unit(action),
        )


def test_host_control_rejects_arbitrary_action() -> None:
    with pytest.raises(ValueError):
        host_control_command(cast(PrivilegedHostAction, "arbitrary.service"))
