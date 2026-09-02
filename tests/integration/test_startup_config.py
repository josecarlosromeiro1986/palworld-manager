import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "command",
    (
        (sys.executable, "-c", "import app.main"),
        (sys.executable, "-m", "app.worker"),
    ),
)
def test_invalid_config_prevents_startup_without_exposing_input(
    command: tuple[str, ...],
) -> None:
    sensitive_value = "valor-privado-nao-exibir"
    environment = os.environ.copy()
    environment["APP_PORT"] = sensitive_value

    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert sensitive_value not in result.stdout
    assert sensitive_value not in result.stderr


@pytest.mark.parametrize(
    "command",
    (
        (sys.executable, "-c", "import app.main"),
        (sys.executable, "-m", "app.worker"),
    ),
)
def test_missing_rest_credentials_prevent_production_startup(
    command: tuple[str, ...],
) -> None:
    environment = os.environ.copy()
    environment["APP_ENVIRONMENT"] = "production"
    environment["APP_HOST"] = "127.0.0.1"
    environment.pop("PALWORLD_REST_USERNAME", None)
    environment.pop("PALWORLD_REST_PASSWORD", None)

    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "PALWORLD_REST_USERNAME" in output
    assert "admin" not in output.lower()
