import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Job

AUDIT_ORIGIN_ADMINISTRATOR = "ADMINISTRATOR"
AUDIT_ORIGIN_AUTOMATIC = "AUTOMATIC"
AUDIT_ORIGIN_SYSTEM = "SYSTEM"
AUDIT_RESULT_FAILURE = "FAILURE"
AUDIT_RESULT_SUCCESS = "SUCCESS"
AUDIT_RESULT_CANCELLED = "CANCELLED"
AUDIT_RESULT_INTERRUPTED = "INTERRUPTED"
AUDIT_RETENTION_DAYS: Final = 90
AUDIT_ACTION_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
AUDIT_ORIGINS: Final = frozenset(
    {AUDIT_ORIGIN_ADMINISTRATOR, AUDIT_ORIGIN_AUTOMATIC, AUDIT_ORIGIN_SYSTEM}
)
AUDIT_RESULTS: Final = frozenset(
    {
        AUDIT_RESULT_SUCCESS,
        AUDIT_RESULT_FAILURE,
        AUDIT_RESULT_CANCELLED,
        AUDIT_RESULT_INTERRUPTED,
    }
)
SENSITIVE_DETAIL_KEY_PATTERN: Final = re.compile(
    r"(?i)(password|passwd|secret|token|webhook|authorization|cookie|credential)"
)
SENSITIVE_ASSIGNMENT_PATTERN: Final = re.compile(
    r"(?i)\b(password|passwd|secret|token|webhook|authorization|cookie|credential)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
PROTECTED_VALUE: Final = "[SEGREDO PROTEGIDO]"
OMITTED_DETAILS: Final = "DETALHES_OMITIDOS_POR_LIMITE"
MAX_AUDIT_DETAIL_DEPTH: Final = 6
MAX_AUDIT_DETAIL_ITEMS: Final = 100
MAX_AUDIT_DETAIL_TEXT: Final = 4_000
MAX_AUDIT_DETAILS_BYTES: Final = 16 * 1024


def redact_audit_text(value: str, sensitive_values: Sequence[str] = ()) -> str:
    redacted = SENSITIVE_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{PROTECTED_VALUE}",
        value,
    )
    for sensitive in sorted({item for item in sensitive_values if item}, key=len, reverse=True):
        redacted = redacted.replace(sensitive, PROTECTED_VALUE)
    return redacted


def redact_audit_details(
    details: Mapping[str, object] | None,
    sensitive_values: Sequence[str] = (),
) -> dict[str, object] | None:
    if details is None:
        return None
    redacted = _redact_mapping(
        cast(Mapping[object, object], details),
        sensitive_values,
        depth=0,
    )
    encoded = json.dumps(redacted, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_AUDIT_DETAILS_BYTES:
        return {"status": OMITTED_DETAILS}
    return redacted


def prune_expired_audit_events(session: Session, *, now: datetime) -> int:
    current = _aware_utc(now)
    result = session.execute(
        delete(AuditEvent).where(
            AuditEvent.occurred_at < current - timedelta(days=AUDIT_RETENTION_DAYS)
        )
    )
    rowcount = getattr(result, "rowcount", 0)
    return max(rowcount if isinstance(rowcount, int) else 0, 0)


def record_audit_event(
    session: Session,
    *,
    occurred_at: datetime,
    action: str,
    result: str,
    origin: str,
    user_id: int | None = None,
    job_id: int | None = None,
    target: str | None = None,
    reason: str | None = None,
    details: dict[str, object] | None = None,
    duration_ms: int | None = None,
) -> AuditEvent:
    timestamp = _aware_utc(occurred_at)
    if AUDIT_ACTION_PATTERN.fullmatch(action) is None:
        raise ValueError("ação de auditoria inválida")
    if result not in AUDIT_RESULTS:
        raise ValueError("resultado de auditoria inválido")
    if origin not in AUDIT_ORIGINS:
        raise ValueError("origem de auditoria inválida")
    if duration_ms is None and job_id is not None:
        duration_ms = _job_duration_ms(session, job_id)
    if isinstance(duration_ms, bool) or (duration_ms is not None and duration_ms < 0):
        raise ValueError("duração de auditoria inválida")
    prune_expired_audit_events(session, now=timestamp)
    event = AuditEvent(
        occurred_at=timestamp,
        action=action,
        result=result,
        origin=origin,
        user_id=user_id,
        job_id=job_id,
        target=redact_audit_text(target) if target is not None else None,
        reason=redact_audit_text(reason) if reason is not None else None,
        duration_ms=duration_ms,
        details=redact_audit_details(details),
    )
    session.add(event)
    return event


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp de auditoria deve conter timezone")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _job_duration_ms(session: Session, job_id: int) -> int | None:
    job = session.get(Job, job_id)
    if job is None or job.started_at is None or job.finished_at is None:
        return None
    duration = _stored_utc(job.finished_at) - _stored_utc(job.started_at)
    if duration.total_seconds() < 0:
        return None
    return int(duration.total_seconds() * 1_000)


def _redact_mapping(
    value: Mapping[object, object],
    sensitive_values: Sequence[str],
    *,
    depth: int,
) -> dict[str, object]:
    if depth >= MAX_AUDIT_DETAIL_DEPTH:
        return {"status": OMITTED_DETAILS}
    result: dict[str, object] = {}
    for index, (raw_key, nested) in enumerate(value.items()):
        if index >= MAX_AUDIT_DETAIL_ITEMS:
            result["status"] = OMITTED_DETAILS
            break
        key = redact_audit_text(str(raw_key), sensitive_values)[:100]
        result[key] = (
            PROTECTED_VALUE
            if SENSITIVE_DETAIL_KEY_PATTERN.search(key)
            else _redact_value(nested, sensitive_values, depth=depth + 1)
        )
    return result


def _redact_value(value: object, sensitive_values: Sequence[str], *, depth: int) -> object:
    if isinstance(value, Mapping):
        return _redact_mapping(value, sensitive_values, depth=depth)
    if isinstance(value, (list, tuple)):
        if depth >= MAX_AUDIT_DETAIL_DEPTH:
            return OMITTED_DETAILS
        return [
            _redact_value(item, sensitive_values, depth=depth + 1)
            for item in value[:MAX_AUDIT_DETAIL_ITEMS]
        ]
    if isinstance(value, str):
        return redact_audit_text(value, sensitive_values)[:MAX_AUDIT_DETAIL_TEXT]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_audit_text(str(value), sensitive_values)[:MAX_AUDIT_DETAIL_TEXT]
