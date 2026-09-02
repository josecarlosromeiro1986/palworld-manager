from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.csrf import generate_token, hash_token, token_matches_hash
from app.auth.roles import UserRole
from app.db.models import SessionRecord, User

SESSION_MAXIMUM_DURATION = timedelta(hours=8)
SESSION_INACTIVITY_TIMEOUT = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    session_id: int
    user_id: int
    username: str
    role: UserRole
    password_change_required: bool
    csrf_token_hash: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def issue_session(session: Session, user: User, *, now: datetime | None = None) -> IssuedSession:
    issued_at = now or datetime.now(UTC)
    session_token = generate_token()
    csrf_token = generate_token()
    expires_at = issued_at + SESSION_MAXIMUM_DURATION
    record = SessionRecord(
        user_id=user.id,
        token_hash=hash_token(session_token),
        csrf_token_hash=hash_token(csrf_token),
        expires_at=expires_at,
        last_seen_at=issued_at,
    )
    session.add(record)
    session.flush()
    return IssuedSession(
        session_token=session_token,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


def resolve_session(
    session: Session,
    session_token: str | None,
    *,
    now: datetime | None = None,
) -> SessionPrincipal | None:
    if session_token is None:
        return None

    current_time = now or datetime.now(UTC)
    result = session.execute(
        select(SessionRecord, User)
        .join(User, User.id == SessionRecord.user_id)
        .where(SessionRecord.token_hash == hash_token(session_token))
    ).one_or_none()
    if result is None:
        return None

    record, user = result
    expired = _utc(record.expires_at) <= current_time
    inactive = _utc(record.last_seen_at) + SESSION_INACTIVITY_TIMEOUT <= current_time
    if record.revoked_at is not None or expired or inactive or not user.is_active:
        if record.revoked_at is None:
            record.revoked_at = current_time
            session.flush()
        return None

    record.last_seen_at = current_time
    session.flush()
    return SessionPrincipal(
        session_id=record.id,
        user_id=user.id,
        username=user.username,
        role=UserRole(user.role),
        password_change_required=user.password_change_required,
        csrf_token_hash=record.csrf_token_hash,
    )


def session_csrf_is_valid(principal: SessionPrincipal, csrf_token: str | None) -> bool:
    return token_matches_hash(csrf_token, principal.csrf_token_hash)


def revoke_session(
    session: Session,
    session_id: int,
    *,
    now: datetime | None = None,
) -> None:
    record = session.get(SessionRecord, session_id)
    if record is not None and record.revoked_at is None:
        record.revoked_at = now or datetime.now(UTC)
        session.flush()


def revoke_user_sessions(
    session: Session,
    user_id: int,
    *,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    records = session.scalars(
        select(SessionRecord).where(
            SessionRecord.user_id == user_id,
            SessionRecord.revoked_at.is_(None),
        )
    )
    for record in records:
        record.revoked_at = current_time
    session.flush()
