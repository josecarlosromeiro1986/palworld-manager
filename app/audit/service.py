from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import AuditEvent

AUDIT_ORIGIN_ADMINISTRATOR = "ADMINISTRATOR"
AUDIT_ORIGIN_SYSTEM = "SYSTEM"
AUDIT_RESULT_FAILURE = "FAILURE"
AUDIT_RESULT_SUCCESS = "SUCCESS"
AUDIT_RESULT_CANCELLED = "CANCELLED"


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
) -> AuditEvent:
    event = AuditEvent(
        occurred_at=occurred_at,
        action=action,
        result=result,
        origin=origin,
        user_id=user_id,
        job_id=job_id,
        target=target,
        reason=reason,
        duration_ms=None,
        details=details,
    )
    session.add(event)
    return event
