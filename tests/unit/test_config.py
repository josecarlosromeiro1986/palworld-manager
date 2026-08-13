from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppEnvironment, Settings

CONFIG_ENV_VARS = (
    "APP_ENVIRONMENT",
    "PALWORLD_SERVICE",
    "PALWORLD_REST_BASE_URL",
    "PALWORLD_REST_USERNAME",
    "PALWORLD_REST_PASSWORD",
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
    assert str(settings.palworld_rest_base_url) == "http://127.0.0.1:8212/v1/api"
    assert settings.palworld_rest_username is None
    assert settings.palworld_rest_password is None
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
    if environment is AppEnvironment.PRODUCTION:
        monkeypatch.setenv("PALWORLD_REST_USERNAME", "usuario-ficticio")
        monkeypatch.setenv("PALWORLD_REST_PASSWORD", "senha-ficticia")

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


@pytest.mark.parametrize(
    ("missing_variable", "error_message"),
    [
        ("PALWORLD_REST_USERNAME", "PALWORLD_REST_USERNAME é obrigatório"),
        ("PALWORLD_REST_PASSWORD", "PALWORLD_REST_PASSWORD é obrigatório"),
    ],
)
def test_production_requires_each_rest_secret(
    missing_variable: str,
    error_message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("PALWORLD_REST_USERNAME", "usuario-ficticio")
    monkeypatch.setenv("PALWORLD_REST_PASSWORD", "senha-ficticia")
    monkeypatch.delenv(missing_variable)

    with pytest.raises(ValidationError, match=error_message):
        Settings()


def test_production_does_not_assume_admin_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("PALWORLD_REST_PASSWORD", "senha-ficticia")

    with pytest.raises(ValidationError, match="PALWORLD_REST_USERNAME"):
        Settings()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PALWORLD_REST_USERNAME", "usuario:invalido"),
        ("PALWORLD_REST_USERNAME", "usuario\ninvalido"),
        ("PALWORLD_REST_PASSWORD", "senha\ninvalida"),
    ],
)
def test_production_rejects_invalid_rest_credentials(
    variable: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("PALWORLD_REST_USERNAME", "usuario-ficticio")
    monkeypatch.setenv("PALWORLD_REST_PASSWORD", "senha-ficticia")
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError, match="formato inválido"):
        Settings()


@pytest.mark.parametrize(
    "base_url",
    [
        "not-a-url",
        "http://usuario:senha@127.0.0.1:8212/v1/api",
        "http://127.0.0.1:8212/v1/api?secret=value",
    ],
)
def test_rest_base_url_rejects_invalid_or_sensitive_parts(
    base_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PALWORLD_REST_BASE_URL", base_url)

    with pytest.raises(ValidationError, match=r"PALWORLD_REST_BASE_URL|URL"):
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
