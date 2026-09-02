from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_ORIGIN_SYSTEM,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.auth.passwords import verify_password_or_dummy
from app.auth.roles import username_key
from app.auth.service import MAXIMUM_USERNAME_LENGTH
from app.db.models import LoginAttempt, User
from app.notifications.service import LOGIN_BLOCKED, enqueue_discord_notification

MAXIMUM_FAILED_ATTEMPTS = 5
LOGIN_BLOCK_DURATION = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User | None
    blocked_until: datetime | None

    @property
    def authenticated(self) -> bool:
        return self.user is not None

    @property
    def blocked(self) -> bool:
        return self.blocked_until is not None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _tracked_username(username: str) -> str | None:
    normalized = username.strip()
    if not normalized:
        return None
    return username_key(normalized[:MAXIMUM_USERNAME_LENGTH])


def _attempts_for_username(username: str | None) -> Select[tuple[LoginAttempt]]:
    query = select(LoginAttempt)
    if username is None:
        return query.where(LoginAttempt.username.is_(None))
    return query.where(LoginAttempt.username == username)


def _active_block(
    session: Session,
    username: str | None,
    now: datetime,
) -> datetime | None:
    latest = session.scalar(
        _attempts_for_username(username)
        .where(LoginAttempt.blocked_until.is_not(None))
        .order_by(LoginAttempt.attempted_at.desc(), LoginAttempt.id.desc())
        .limit(1)
    )
    if latest is None or latest.blocked_until is None:
        return None
    blocked_until = _utc(latest.blocked_until)
    return blocked_until if blocked_until > now else None


def _consecutive_failures(session: Session, username: str | None) -> int:
    attempts = session.scalars(
        _attempts_for_username(username)
        .order_by(LoginAttempt.attempted_at.desc(), LoginAttempt.id.desc())
        .limit(MAXIMUM_FAILED_ATTEMPTS)
    )
    failures = 0
    for attempt in attempts:
        if attempt.successful or attempt.blocked_until is not None:
            break
        failures += 1
    return failures


def _audit_details(source_address: str | None) -> dict[str, object] | None:
    if source_address is None:
        return None
    return {"source_address": source_address}


def attempt_user_login(
    session: Session,
    username: str,
    password: str,
    source_address: str | None,
    *,
    now: datetime | None = None,
) -> LoginResult:
    current_time = now or datetime.now(UTC)
    tracked_username = _tracked_username(username)

    # Serialize read/decide/write so concurrent requests cannot bypass the limit.
    session.execute(text("BEGIN IMMEDIATE"))

    user = session.scalar(
        select(User).where(
            User.username_key == tracked_username,
            User.is_active.is_(True),
        )
    )
    user_id = user.id if user is not None else None
    details = _audit_details(source_address)

    blocked_until = _active_block(session, tracked_username, current_time)
    if blocked_until is not None:
        session.add(
            LoginAttempt(
                user_id=user_id,
                username=tracked_username,
                source_address=source_address,
                successful=False,
                attempted_at=current_time,
                blocked_until=blocked_until,
            )
        )
        record_audit_event(
            session,
            occurred_at=current_time,
            action="LOGIN",
            result=AUDIT_RESULT_FAILURE,
            origin=AUDIT_ORIGIN_ADMINISTRATOR,
            user_id=user_id,
            target=tracked_username,
            reason="BLOCKED",
            details=details,
        )
        session.flush()
        return LoginResult(user=None, blocked_until=blocked_until)

    password_hash = user.password_hash if user is not None else None
    if verify_password_or_dummy(password, password_hash) and user is not None:
        session.add(
            LoginAttempt(
                user_id=user.id,
                username=tracked_username,
                source_address=source_address,
                successful=True,
                attempted_at=current_time,
                blocked_until=None,
            )
        )
        record_audit_event(
            session,
            occurred_at=current_time,
            action="LOGIN",
            result=AUDIT_RESULT_SUCCESS,
            origin=AUDIT_ORIGIN_ADMINISTRATOR,
            user_id=user.id,
            target=tracked_username,
            details=details,
        )
        session.flush()
        return LoginResult(user=user, blocked_until=None)

    failure_count = _consecutive_failures(session, tracked_username) + 1
    new_block = (
        current_time + LOGIN_BLOCK_DURATION if failure_count >= MAXIMUM_FAILED_ATTEMPTS else None
    )
    session.add(
        LoginAttempt(
            user_id=user_id,
            username=tracked_username,
            source_address=source_address,
            successful=False,
            attempted_at=current_time,
            blocked_until=new_block,
        )
    )
    record_audit_event(
        session,
        occurred_at=current_time,
        action="LOGIN",
        result=AUDIT_RESULT_FAILURE,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        target=tracked_username,
        reason="INVALID_CREDENTIALS",
        details=details,
    )
    if new_block is not None:
        record_audit_event(
            session,
            occurred_at=current_time,
            action="LOGIN_BLOCKED",
            result=AUDIT_RESULT_SUCCESS,
            origin=AUDIT_ORIGIN_SYSTEM,
            user_id=user_id,
            target=tracked_username,
            details=details,
        )
        enqueue_discord_notification(session, LOGIN_BLOCKED, created_at=current_time)
    session.flush()
    return LoginResult(user=None, blocked_until=new_block)


# Compatibilidade com integrações e scripts existentes.
attempt_administrator_login = attempt_user_login
