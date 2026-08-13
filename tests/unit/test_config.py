from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppEnvironment, Settings

CONFIG_ENV_VARS = (
    "APP_ENVIRONMENT",
    "PALWORLD_SERVICE",
    "PALWORLD_DIR",
    "PALWORLD_SETTINGS",
    "STEAMCMD",
    "APP_HOST",
    "APP_PORT",
    "MANAGER_DATABASE",
)


@pytest.fixture(autouse=True)
def clean_config_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for variable in CONFIG_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    yield


def test_structural_defaults() -> None:
    settings = Settings()

    assert settings.environment is AppEnvironment.DEVELOPMENT
    assert settings.palworld_service == "palworld.service"
    assert settings.palworld_dir == Path("/home/steam/palserver")
    assert settings.app_host.is_loopback
    assert settings.app_port == 8080
    assert settings.manager_database == Path("/var/lib/palworld-manager/manager.db")


@pytest.mark.parametrize("environment", list(AppEnvironment))
def test_supported_environments(
    environment: AppEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", environment.value)

    assert Settings().environment is environment


def test_environment_variable_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("APP_ENVIRONMENT=production\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENVIRONMENT", "test")

    assert Settings().environment is AppEnvironment.TEST


def test_production_requires_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_HOST", "0.0.0.0")

    with pytest.raises(ValidationError, match="loopback"):
        Settings()


def test_paths_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEAMCMD", "bin/steamcmd")

    with pytest.raises(ValidationError, match="absoluto"):
        Settings()


@pytest.mark.parametrize(
    "service_name",
    ["--all.service", "palworld.service --no-pager", "../palworld.service", "palworld"],
)
def test_palworld_service_name_rejects_command_options(
    service_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PALWORLD_SERVICE", service_name)

    with pytest.raises(ValidationError):
        Settings()


def test_validation_error_hides_input(monkeypatch: pytest.MonkeyPatch) -> None:
    sensitive_value = "valor-privado-nao-exibir"
    monkeypatch.setenv("APP_PORT", sensitive_value)

    with pytest.raises(ValidationError) as error:
        Settings()

    assert sensitive_value not in str(error.value)
