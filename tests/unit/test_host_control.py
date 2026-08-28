import os
import stat
from pathlib import Path
from typing import cast

import pytest

from app.system.host_control import (
    FileHostControlRequester,
    HostControlRequestError,
    PrivilegedHostAction,
    host_control_request_name,
)


def test_host_control_maps_every_closed_action_to_one_exact_request() -> None:
    requests = {host_control_request_name(action) for action in PrivilegedHostAction}

    assert requests == {
        "palworld-start.request",
        "palworld-stop.request",
        "palworld-restart.request",
        "palworld-sigterm.request",
        "palworld-sigkill.request",
        "host-reboot.request",
        "host-poweroff.request",
    }


def test_file_requester_creates_one_empty_exclusive_request(tmp_path: Path) -> None:
    request_directory = tmp_path / "host-control"
    request_directory.mkdir(mode=0o770)
    request_directory.chmod(0o770)
    requester = FileHostControlRequester(
        request_directory,
        expected_owner_uid=os.geteuid(),
        expected_group_gid=os.getegid(),
    )

    requester(PrivilegedHostAction.PALWORLD_START)

    request = request_directory / "palworld-start.request"
    assert request.read_bytes() == b""
    assert stat.S_IMODE(request.stat().st_mode) == 0o600
    with pytest.raises(HostControlRequestError):
        requester(PrivilegedHostAction.PALWORLD_START)


def test_file_requester_rejects_insecure_request_directory(tmp_path: Path) -> None:
    request_directory = tmp_path / "host-control"
    request_directory.mkdir(mode=0o777)
    request_directory.chmod(0o777)
    requester = FileHostControlRequester(
        request_directory,
        expected_owner_uid=os.geteuid(),
        expected_group_gid=os.getegid(),
    )

    with pytest.raises(HostControlRequestError, match="inseguro"):
        requester(PrivilegedHostAction.PALWORLD_START)

    assert list(request_directory.iterdir()) == []


def test_host_control_rejects_arbitrary_action() -> None:
    with pytest.raises(ValueError):
        host_control_request_name(cast(PrivilegedHostAction, "arbitrary.service"))
