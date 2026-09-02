import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Final
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ACTION_PATTERN,
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_AUTOMATIC,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_ORIGINS,
    AUDIT_RESULT_CANCELLED,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_INTERRUPTED,
    AUDIT_RESULT_SUCCESS,
    AUDIT_RESULTS,
    prune_expired_audit_events,
    redact_audit_details,
    redact_audit_text,
)
from app.config import Settings
from app.db.engine import session_scope
from app.db.models import AuditEvent, User
from app.manager_settings.service import load_manager_settings

AUDIT_PAGE_SIZE: Final = 50
MAX_TARGET_FILTER_LENGTH: Final = 255

ORIGIN_LABELS: Final = {
    AUDIT_ORIGIN_ADMINISTRATOR: "Administrador",
    AUDIT_ORIGIN_AUTOMATIC: "Automático",
    AUDIT_ORIGIN_SYSTEM: "Sistema",
}
RESULT_LABELS: Final = {
    AUDIT_RESULT_SUCCESS: "Sucesso",
    AUDIT_RESULT_FAILURE: "Falha",
    AUDIT_RESULT_CANCELLED: "Cancelada",
    AUDIT_RESULT_INTERRUPTED: "Interrompida",
}


class AuditFilterError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuditFilters:
    date_from: date | None = None
    date_to: date | None = None
    action: str | None = None
    result: str | None = None
    origin: str | None = None
    user_id: int | None = None
    target: str | None = None
    page: int = 1

    def query_parameters(self, *, page: int | None = None) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        if self.date_from is not None:
            values.append(("date_from", self.date_from.isoformat()))
        if self.date_to is not None:
            values.append(("date_to", self.date_to.isoformat()))
        if self.action is not None:
            values.append(("action", self.action))
        if self.result is not None:
            values.append(("result", self.result))
        if self.origin is not None:
            values.append(("origin", self.origin))
        if self.user_id is not None:
            values.append(("user_id", str(self.user_id)))
        if self.target is not None:
            values.append(("target", self.target))
        selected_page = self.page if page is None else page
        if selected_page > 1:
            values.append(("page", str(selected_page)))
        return values


@dataclass(frozen=True, slots=True)
class AuditUserOption:
    id: int
    username: str


@dataclass(frozen=True, slots=True)
class AuditEventView:
    id: int
    occurred_at_iso: str
    occurred_at_display: str
    action: str
    result: str
    result_label: str
    origin: str
    origin_label: str
    username: str
    target: str
    reason: str | None
    duration: str | None
    details: str | None
    job_id: int | None


@dataclass(frozen=True, slots=True)
class AuditHistoryPage:
    events: tuple[AuditEventView, ...]
    filters: AuditFilters
    actions: tuple[str, ...]
    users: tuple[AuditUserOption, ...]
    total_items: int
    page: int
    total_pages: int
    timezone_name: str

    @property
    def previous_url(self) -> str | None:
        return self._page_url(self.page - 1) if self.page > 1 else None

    @property
    def next_url(self) -> str | None:
        return self._page_url(self.page + 1) if self.page < self.total_pages else None

    def _page_url(self, page: int) -> str:
        query = urlencode(self.filters.query_parameters(page=page))
        return f"/audit?{query}" if query else "/audit"


def parse_audit_filters(
    *,
    date_from: str | None,
    date_to: str | None,
    action: str | None,
    result: str | None,
    origin: str | None,
    user_id: str | None,
    target: str | None,
    page: str | None,
) -> AuditFilters:
    parsed_from = _optional_date(date_from, "Data inicial")
    parsed_to = _optional_date(date_to, "Data final")
    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        raise AuditFilterError("A data inicial não pode ser posterior à data final.")
    parsed_action = _optional_text(action)
    if parsed_action is not None and AUDIT_ACTION_PATTERN.fullmatch(parsed_action) is None:
        raise AuditFilterError("A ação informada é inválida.")
    parsed_result = _optional_text(result)
    if parsed_result is not None and parsed_result not in AUDIT_RESULTS:
        raise AuditFilterError("O resultado informado é inválido.")
    parsed_origin = _optional_text(origin)
    if parsed_origin is not None and parsed_origin not in AUDIT_ORIGINS:
        raise AuditFilterError("A origem informada é inválida.")
    parsed_user = _optional_positive_int(user_id, "O usuário informado é inválido.")
    parsed_target = _optional_text(target)
    if parsed_target is not None and len(parsed_target) > MAX_TARGET_FILTER_LENGTH:
        raise AuditFilterError("O alvo deve ter no máximo 255 caracteres.")
    parsed_page = _optional_positive_int(page, "A página informada é inválida.") or 1
    return AuditFilters(
        date_from=parsed_from,
        date_to=parsed_to,
        action=parsed_action,
        result=parsed_result,
        origin=parsed_origin,
        user_id=parsed_user,
        target=parsed_target,
        page=parsed_page,
    )


class AuditHistoryService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def search(self, filters: AuditFilters) -> AuditHistoryPage:
        now = _aware_utc(self._clock())
        sensitive_values = _sensitive_values(self._settings)
        with session_scope(self._session_factory) as session:
            prune_expired_audit_events(session, now=now)
            timezone_name = load_manager_settings(session).values.timezone
            timezone = ZoneInfo(timezone_name)
            predicates = _filter_predicates(filters, timezone)
            total_items = session.scalar(select(func.count(AuditEvent.id)).where(*predicates)) or 0
            total_pages = max(1, ceil(total_items / AUDIT_PAGE_SIZE))
            selected_page = min(filters.page, total_pages)
            rows = session.execute(
                select(AuditEvent, User.username)
                .outerjoin(User, AuditEvent.user_id == User.id)
                .where(*predicates)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .offset((selected_page - 1) * AUDIT_PAGE_SIZE)
                .limit(AUDIT_PAGE_SIZE)
            ).all()
            actions = tuple(
                session.scalars(select(AuditEvent.action).distinct().order_by(AuditEvent.action))
            )
            users = tuple(
                AuditUserOption(user_id_value, username)
                for user_id_value, username in session.execute(
                    select(User.id, User.username)
                    .join(AuditEvent, AuditEvent.user_id == User.id)
                    .distinct()
                    .order_by(User.username, User.id)
                )
            )
        events = tuple(
            _event_view(event, username, timezone, sensitive_values) for event, username in rows
        )
        normalized_filters = AuditFilters(
            date_from=filters.date_from,
            date_to=filters.date_to,
            action=filters.action,
            result=filters.result,
            origin=filters.origin,
            user_id=filters.user_id,
            target=filters.target,
            page=selected_page,
        )
        return AuditHistoryPage(
            events=events,
            filters=normalized_filters,
            actions=actions,
            users=users,
            total_items=total_items,
            page=selected_page,
            total_pages=total_pages,
            timezone_name=timezone_name,
        )


def _filter_predicates(
    filters: AuditFilters,
    timezone: ZoneInfo,
) -> list[ColumnElement[bool]]:
    predicates: list[ColumnElement[bool]] = []
    if filters.date_from is not None:
        predicates.append(
            AuditEvent.occurred_at
            >= datetime.combine(filters.date_from, time.min, timezone).astimezone(UTC)
        )
    if filters.date_to is not None:
        next_day = filters.date_to + timedelta(days=1)
        predicates.append(
            AuditEvent.occurred_at < datetime.combine(next_day, time.min, timezone).astimezone(UTC)
        )
    if filters.action is not None:
        predicates.append(AuditEvent.action == filters.action)
    if filters.result is not None:
        predicates.append(AuditEvent.result == filters.result)
    if filters.origin is not None:
        predicates.append(AuditEvent.origin == filters.origin)
    if filters.user_id is not None:
        predicates.append(AuditEvent.user_id == filters.user_id)
    if filters.target is not None:
        escaped = filters.target.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        predicates.append(AuditEvent.target.ilike(f"%{escaped}%", escape="\\"))
    return predicates


def _event_view(
    event: AuditEvent,
    username: str | None,
    timezone: ZoneInfo,
    sensitive_values: tuple[str, ...],
) -> AuditEventView:
    occurred_at = _stored_utc(event.occurred_at)
    details = redact_audit_details(event.details, sensitive_values)
    return AuditEventView(
        id=event.id,
        occurred_at_iso=occurred_at.isoformat(),
        occurred_at_display=occurred_at.astimezone(timezone).strftime("%d/%m/%Y %H:%M:%S %Z"),
        action=event.action,
        result=event.result,
        result_label=RESULT_LABELS.get(event.result, event.result),
        origin=event.origin,
        origin_label=ORIGIN_LABELS.get(event.origin, event.origin),
        username=redact_audit_text(username, sensitive_values) if username else "—",
        target=redact_audit_text(event.target, sensitive_values) if event.target else "—",
        reason=(redact_audit_text(event.reason, sensitive_values) if event.reason else None),
        duration=_duration_label(event.duration_ms),
        details=(json.dumps(details, ensure_ascii=False, sort_keys=True) if details else None),
        job_id=event.job_id,
    )


def _duration_label(duration_ms: int | None) -> str | None:
    if duration_ms is None:
        return None
    if duration_ms < 1_000:
        return f"{duration_ms} ms"
    return f"{duration_ms / 1_000:.2f} s"


def _optional_date(value: str | None, label: str) -> date | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError as error:
        raise AuditFilterError(f"{label} deve usar uma data válida.") from error


def _optional_positive_int(value: str | None, message: str) -> int | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    if not normalized.isdecimal() or int(normalized) < 1:
        raise AuditFilterError(message)
    return int(normalized)


def _optional_text(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


def _sensitive_values(settings: Settings) -> tuple[str, ...]:
    values: list[str] = []
    for secret in (
        settings.palworld_rest_username,
        settings.palworld_rest_password,
        settings.discord_webhook_url,
    ):
        if secret is not None:
            values.append(secret.get_secret_value())
    return tuple(values)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("o relógio da auditoria deve retornar timezone")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
