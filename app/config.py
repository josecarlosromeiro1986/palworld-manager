from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, StringConstraints, field_validator, model_validator
from pydantic.networks import IPvAnyAddress
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.@-]*\.service$"

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
    app_host: IPvAnyAddress = Field(
        default=ip_address("127.0.0.1"),
        validation_alias="APP_HOST",
    )
    app_port: Port = Field(default=8080, validation_alias="APP_PORT")
    manager_database: Path = Field(
        default=Path("/var/lib/palworld-manager/manager.db"),
        validation_alias="MANAGER_DATABASE",
    )

    @field_validator("palworld_dir", "palworld_settings", "steamcmd", "manager_database")
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("o caminho deve ser absoluto")
        return value

    @model_validator(mode="after")
    def require_loopback_in_production(self) -> Self:
        if self.environment is AppEnvironment.PRODUCTION and not self.app_host.is_loopback:
            raise ValueError("APP_HOST deve ser um endereço de loopback em production")
        return self
