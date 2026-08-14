from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.backups.jobs import BackupJobConflictError, enqueue_local_backup
from app.db.models import AppSetting

BACKUP_ENABLED_KEY = "backup_enabled"
BACKUP_TIME_KEY = "backup_time"
TIMEZONE_KEY = "timezone"
LAST_SCHEDULED_DATE_KEY = "backup_last_scheduled_local_date"
DEFAULT_BACKUP_TIME = "04:00"
DEFAULT_TIMEZONE = "America/Sao_Paulo"


def schedule_daily_backup(session: Session, *, now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if not _boolean_setting(session, BACKUP_ENABLED_KEY, True):
        return False
    timezone = _timezone_setting(session)
    scheduled_time = _time_setting(session)
    local_now = current.astimezone(timezone)
    if local_now.timetz().replace(tzinfo=None) < scheduled_time:
        return False
    local_date = local_now.date().isoformat()
    marker = session.get(AppSetting, LAST_SCHEDULED_DATE_KEY)
    if marker is not None and marker.value == local_date:
        return False
    try:
        enqueue_local_backup(
            session,
            user_id=None,
            trigger="AUTOMATIC",
            occurred_at=current,
        )
    except BackupJobConflictError:
        return False
    if marker is None:
        session.add(AppSetting(key=LAST_SCHEDULED_DATE_KEY, value=local_date))
    else:
        marker.value = local_date
    return True


def _boolean_setting(session: Session, key: str, default: bool) -> bool:
    setting = session.get(AppSetting, key)
    if setting is None:
        return default
    if not isinstance(setting.value, bool):
        raise ValueError(f"{key} possui valor inválido")
    return setting.value


def _timezone_setting(session: Session) -> ZoneInfo:
    setting = session.get(AppSetting, TIMEZONE_KEY)
    value = DEFAULT_TIMEZONE if setting is None else setting.value
    if not isinstance(value, str):
        raise ValueError("timezone possui valor inválido")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError("timezone possui valor inválido") from error


def _time_setting(session: Session) -> time:
    setting = session.get(AppSetting, BACKUP_TIME_KEY)
    value = DEFAULT_BACKUP_TIME if setting is None else setting.value
    if not isinstance(value, str):
        raise ValueError("backup_time possui valor inválido")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise ValueError("backup_time possui valor inválido") from error
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("backup_time possui valor inválido")
    return parsed
