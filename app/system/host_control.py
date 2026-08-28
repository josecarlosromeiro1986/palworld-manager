from enum import StrEnum

SYSTEMCTL_PATH = "/usr/bin/systemctl"
MANAGED_PALWORLD_SERVICE = "palworld.service"
HOST_CONTROL_UNIT_PREFIX = "palworld-manager-host-control"


class PrivilegedHostAction(StrEnum):
    PALWORLD_START = "palworld-start"
    PALWORLD_STOP = "palworld-stop"
    PALWORLD_RESTART = "palworld-restart"
    PALWORLD_SIGTERM = "palworld-sigterm"
    PALWORLD_SIGKILL = "palworld-sigkill"
    HOST_REBOOT = "host-reboot"
    HOST_POWEROFF = "host-poweroff"


def host_control_unit(action: PrivilegedHostAction) -> str:
    if not isinstance(action, PrivilegedHostAction):
        raise ValueError("ação privilegiada do host inválida")
    return f"{HOST_CONTROL_UNIT_PREFIX}@{action.value}.service"


def host_control_command(action: PrivilegedHostAction) -> tuple[str, ...]:
    return (
        SYSTEMCTL_PATH,
        "--no-ask-password",
        "start",
        host_control_unit(action),
    )
