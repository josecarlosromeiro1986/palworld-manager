import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.db.models import AppSetting, NotificationEvent
from app.notifications.service import (
    DISCORD_TEST,
    NOTIFICATION_CHANNEL_DISCORD,
    NOTIFICATION_STATUS_PENDING,
    NOTIFICATION_STATUS_SENDING,
    enqueue_discord_notification,
)

DEFAULT_MANAGER_SETTINGS: Final[dict[str, object]] = {
    "timezone": "America/Sao_Paulo",
    "backup_enabled": True,
    "backup_time": "04:00",
    "local_backup_retention": 3,
    "drive_backup_retention": 10,
    "metrics_interval_seconds": 5,
    "assisted_shutdown_default_minutes": 5,
    "start_timeout_seconds": 120,
    "restart_timeout_seconds": 120,
    "stop_timeout_seconds": 60,
    "disk_warning_gb": 20,
    "disk_critical_gb": 10,
}
OPERATIONAL_SETTING_KEYS: Final = frozenset(DEFAULT_MANAGER_SETTINGS)
BACKUP_TIME_PATTERN: Final = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class ManagerSettingsError(ValueError):
    pass


class ManagerSettingsValidationError(ManagerSettingsError):
    pass


class ManagerSettingsConflictError(ManagerSettingsError):
    pass


class StoredManagerSettingsError(ManagerSettingsError):
    pass


class ManagerSettingsValues(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    timezone: str = Field(min_length=1, max_length=100)
    backup_enabled: bool
    backup_time: str
    local_backup_retention: int = Field(ge=1, le=30)
    drive_backup_retention: int = Field(ge=1, le=100)
    metrics_interval_seconds: int = Field(ge=1, le=60)
    assisted_shutdown_default_minutes: Literal[0, 1, 5, 10]
    start_timeout_seconds: int = Field(ge=1, le=600)
    restart_timeout_seconds: int = Field(ge=1, le=600)
    stop_timeout_seconds: int = Field(ge=1, le=300)
    disk_warning_gb: int = Field(ge=1, le=1024)
    disk_critical_gb: int = Field(ge=1, le=1024)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("timezone IANA inválido") from error
        return normalized

    @field_validator("backup_time")
    @classmethod
    def validate_backup_time(cls, value: str) -> str:
        if BACKUP_TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("horário deve usar HH:MM")
        return value

    @model_validator(mode="after")
    def validate_disk_thresholds(self) -> "ManagerSettingsValues":
        if self.disk_critical_gb >= self.disk_warning_gb:
            raise ValueError("o limite crítico deve ser menor que o limite de aviso")
        return self


@dataclass(frozen=True, slots=True)
class ManagerSettingsSnapshot:
    values: ManagerSettingsValues
    version: str


def validate_manager_settings(values: dict[str, object]) -> ManagerSettingsValues:
    try:
        return ManagerSettingsValues.model_validate(values)
    except ValidationError as error:
        raise ManagerSettingsValidationError(
            "Revise os valores e limites das configurações operacionais."
        ) from error


def load_manager_settings(session: Session) -> ManagerSettingsSnapshot:
    overrides = {
        setting.key: setting.value
        for setting in session.scalars(
            select(AppSetting).where(AppSetting.key.in_(OPERATIONAL_SETTING_KEYS))
        )
    }
    try:
        values = ManagerSettingsValues.model_validate({**DEFAULT_MANAGER_SETTINGS, **overrides})
    except ValidationError as error:
        raise StoredManagerSettingsError(
            "As configurações operacionais persistidas são inválidas."
        ) from error
    return ManagerSettingsSnapshot(values=values, version=_settings_version(values))


def update_manager_settings(
    session: Session,
    values: ManagerSettingsValues,
    *,
    expected_version: str,
    user_id: int,
    occurred_at: datetime | None = None,
) -> ManagerSettingsSnapshot:
    session.execute(text("BEGIN IMMEDIATE"))
    current = load_manager_settings(session)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_version) or current.version != expected_version:
        raise ManagerSettingsConflictError(
            "As configurações foram alteradas em outra solicitação. Recarregue a página."
        )

    changed_keys = tuple(
        key
        for key in sorted(OPERATIONAL_SETTING_KEYS)
        if getattr(current.values, key) != getattr(values, key)
    )
    changed_at = occurred_at or datetime.now(UTC)
    for key in OPERATIONAL_SETTING_KEYS:
        value = getattr(values, key)
        setting = session.get(AppSetting, key)
        if setting is None:
            session.add(
                AppSetting(
                    key=key,
                    value=value,
                    updated_at=changed_at,
                    updated_by_user_id=user_id,
                )
            )
        elif setting.value != value:
            setting.value = value
            setting.updated_at = changed_at
            setting.updated_by_user_id = user_id
    record_audit_event(
        session,
        occurred_at=changed_at,
        action="MANAGER_SETTINGS_UPDATE",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        target="Configurações do Painel",
        details={"changed_keys": list(changed_keys)},
    )
    session.flush()
    return ManagerSettingsSnapshot(values=values, version=_settings_version(values))


def audit_manager_settings_failure(
    session: Session,
    *,
    user_id: int,
    reason: str,
    occurred_at: datetime | None = None,
) -> None:
    record_audit_event(
        session,
        occurred_at=occurred_at or datetime.now(UTC),
        action="MANAGER_SETTINGS_UPDATE",
        result=AUDIT_RESULT_FAILURE,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        target="Configurações do Painel",
        reason=reason,
        details={"error": reason},
    )


def enqueue_discord_test(
    session: Session,
    *,
    user_id: int,
    occurred_at: datetime | None = None,
) -> NotificationEvent:
    session.execute(text("BEGIN IMMEDIATE"))
    active = session.scalar(
        select(NotificationEvent)
        .where(
            NotificationEvent.event_type == DISCORD_TEST,
            NotificationEvent.channel == NOTIFICATION_CHANNEL_DISCORD,
            NotificationEvent.status.in_(
                (NOTIFICATION_STATUS_PENDING, NOTIFICATION_STATUS_SENDING)
            ),
        )
        .order_by(NotificationEvent.id.desc())
        .limit(1)
    )
    if active is not None:
        return active
    created_at = occurred_at or datetime.now(UTC)
    event = enqueue_discord_notification(session, DISCORD_TEST, created_at=created_at)
    record_audit_event(
        session,
        occurred_at=created_at,
        action="DISCORD_TEST_REQUESTED",
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        target="Discord",
        details={"notification_event_id": event.id},
    )
    return event


def latest_discord_test(session: Session) -> NotificationEvent | None:
    return session.scalar(
        select(NotificationEvent)
        .where(
            NotificationEvent.event_type == DISCORD_TEST,
            NotificationEvent.channel == NOTIFICATION_CHANNEL_DISCORD,
        )
        .order_by(NotificationEvent.id.desc())
        .limit(1)
    )


def configured_local_retention(session: Session) -> int:
    return load_manager_settings(session).values.local_backup_retention


def configured_drive_retention(session: Session) -> int:
    return load_manager_settings(session).values.drive_backup_retention


def configured_metrics_interval(session: Session) -> int:
    return load_manager_settings(session).values.metrics_interval_seconds


def configured_disk_thresholds(session: Session) -> tuple[int, int]:
    values = load_manager_settings(session).values
    return values.disk_warning_gb, values.disk_critical_gb


def _settings_version(values: ManagerSettingsValues) -> str:
    payload = json.dumps(
        values.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
