import os
import stat
from enum import StrEnum
from pathlib import Path
from typing import Protocol

SYSTEMCTL_PATH = "/usr/bin/systemctl"
MANAGED_PALWORLD_SERVICE = "palworld.service"
HOST_CONTROL_REQUEST_DIRECTORY = Path("/run/palworld-manager/host-control")
HOST_CONTROL_REQUEST_DIRECTORY_MODE = 0o770


class PrivilegedHostAction(StrEnum):
    PALWORLD_START = "palworld-start"
    PALWORLD_STOP = "palworld-stop"
    PALWORLD_RESTART = "palworld-restart"
    PALWORLD_SIGTERM = "palworld-sigterm"
    PALWORLD_SIGKILL = "palworld-sigkill"
    HOST_REBOOT = "host-reboot"
    HOST_POWEROFF = "host-poweroff"


class HostControlRequestError(RuntimeError):
    """A solicitação privilegiada não pôde ser registrada com segurança."""


class HostControlRequester(Protocol):
    def __call__(self, action: PrivilegedHostAction) -> None: ...


def _validate_action(action: PrivilegedHostAction) -> None:
    if not isinstance(action, PrivilegedHostAction):
        raise ValueError("ação privilegiada do host inválida")


def host_control_request_name(action: PrivilegedHostAction) -> str:
    _validate_action(action)
    return f"{action.value}.request"


class FileHostControlRequester:
    def __init__(
        self,
        request_directory: Path = HOST_CONTROL_REQUEST_DIRECTORY,
        *,
        expected_owner_uid: int = 0,
        expected_group_gid: int | None = None,
    ) -> None:
        if not request_directory.is_absolute():
            raise ValueError("diretório de solicitações privilegiadas deve ser absoluto")
        self._request_directory = request_directory
        self._expected_owner_uid = expected_owner_uid
        self._expected_group_gid = (
            os.getegid() if expected_group_gid is None else expected_group_gid
        )

    def __call__(self, action: PrivilegedHostAction) -> None:
        request_name = host_control_request_name(action)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        request_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        directory_fd: int | None = None
        request_fd: int | None = None
        try:
            directory_fd = os.open(self._request_directory, directory_flags)
            directory_metadata = os.fstat(directory_fd)
            directory_mode = stat.S_IMODE(directory_metadata.st_mode)
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or directory_metadata.st_uid != self._expected_owner_uid
                or directory_metadata.st_gid != self._expected_group_gid
                or directory_mode != HOST_CONTROL_REQUEST_DIRECTORY_MODE
            ):
                raise HostControlRequestError("Diretório de solicitações privilegiadas inseguro.")
            request_fd = os.open(
                request_name,
                request_flags,
                0o600,
                dir_fd=directory_fd,
            )
        except HostControlRequestError:
            raise
        except OSError as error:
            raise HostControlRequestError(
                "Não foi possível registrar a solicitação privilegiada."
            ) from error
        finally:
            if request_fd is not None:
                os.close(request_fd)
            if directory_fd is not None:
                os.close(directory_fd)


request_host_control = FileHostControlRequester()
