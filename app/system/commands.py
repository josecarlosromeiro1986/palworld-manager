import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

SAFE_ENVIRONMENT_NAMES: Final = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
    "TZ",
)


def sanitized_subprocess_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = os.environ if source is None else source
    return {
        name: value
        for name in SAFE_ENVIRONMENT_NAMES
        if (value := environment.get(name)) is not None and "\x00" not in value
    }


def rclone_subprocess_environment(
    config_path: Path,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if not config_path.is_absolute() or "\x00" in os.fspath(config_path):
        raise ValueError("RCLONE_CONFIG deve usar path absoluto válido")
    environment = sanitized_subprocess_environment(source)
    environment["RCLONE_CONFIG"] = os.fspath(config_path)
    return environment
