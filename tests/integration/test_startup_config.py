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
