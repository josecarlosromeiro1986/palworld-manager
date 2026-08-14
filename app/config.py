from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.networks import IPvAnyAddress
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.@-]*\.service$"
RCLONE_REMOTE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$"

ServiceName = Annotated[str, StringConstraints(pattern=SERVICE_NAME_PATTERN)]
Port = Annotated[int, Field(ge=1, le=65535)]


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
        validate_default=True,
    )

    environment: AppEnvironment = Field(
        default=AppEnvironment.DEVELOPMENT,
        validation_alias="APP_ENVIRONMENT",
    )
    palworld_service: ServiceName = Field(
        default="palworld.service",
        validation_alias="PALWORLD_SERVICE",
    )
    palworld_rest_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://127.0.0.1:8212/v1/api"),
        validation_alias="PALWORLD_REST_BASE_URL",
    )
    palworld_rest_username: SecretStr | None = Field(
        default=None,
        validation_alias="PALWORLD_REST_USERNAME",
    )
    palworld_rest_password: SecretStr | None = Field(
        default=None,
        validation_alias="PALWORLD_REST_PASSWORD",
    )
    palworld_dir: Path = Field(
        default=Path("/home/steam/palserver"),
        validation_alias="PALWORLD_DIR",
    )
    palworld_settings: Path = Field(
        default=Path("/home/steam/palserver/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        validation_alias="PALWORLD_SETTINGS",
    )
    steamcmd: Path = Field(
        default=Path("/usr/games/steamcmd"),
        validation_alias="STEAMCMD",
    )
    rclone: Path = Field(
        default=Path("/usr/bin/rclone"),
        validation_alias="RCLONE",
    )
    rclone_remote: Annotated[str, StringConstraints(pattern=RCLONE_REMOTE_PATTERN)] = Field(
        default="palworld-manager",
        validation_alias="RCLONE_REMOTE",
    )
    app_host: IPvAnyAddress = Field(
        default=ip_address("127.0.0.1"),
        validation_alias="APP_HOST",
    )
    app_port: Port = Field(default=8080, validation_alias="APP_PORT")
    manager_database: Path = Field(
        default=Path("/var/lib/palworld-manager/manager.db"),
        validation_alias="MANAGER_DATABASE",
    )

    @field_validator(
        "palworld_dir",
        "palworld_settings",
        "steamcmd",
        "rclone",
        "manager_database",
    )
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("o caminho deve ser absoluto")
        return value

    @model_validator(mode="after")
    def validate_environment_requirements(self) -> Self:
        rest_url = self.palworld_rest_base_url
        if rest_url.username is not None or rest_url.password is not None:
            raise ValueError(
                "PALWORLD_REST_BASE_URL não pode conter credenciais; use os secrets dedicados"
            )
        if rest_url.query is not None or rest_url.fragment is not None:
            raise ValueError("PALWORLD_REST_BASE_URL não pode conter query string ou fragmento")

        if self.environment is AppEnvironment.PRODUCTION and not self.app_host.is_loopback:
            raise ValueError("APP_HOST deve ser um endereço de loopback em production")

        if self.environment is AppEnvironment.PRODUCTION:
            username = (
                self.palworld_rest_username.get_secret_value()
                if self.palworld_rest_username is not None
                else ""
            )
            password = (
                self.palworld_rest_password.get_secret_value()
                if self.palworld_rest_password is not None
                else ""
            )
            missing_secrets = []
            if not username.strip():
                missing_secrets.append("PALWORLD_REST_USERNAME")
            if not password.strip():
                missing_secrets.append("PALWORLD_REST_PASSWORD")
            if missing_secrets:
                names = " e ".join(missing_secrets)
                agreement = "é obrigatório" if len(missing_secrets) == 1 else "são obrigatórios"
                raise ValueError(f"{names} {agreement} em production")
            if ":" in username or "\r" in username or "\n" in username:
                raise ValueError("PALWORLD_REST_USERNAME possui formato inválido")
            if "\r" in password or "\n" in password:
                raise ValueError("PALWORLD_REST_PASSWORD possui formato inválido")
        return self
