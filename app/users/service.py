from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.auth.passwords import hash_password
from app.auth.roles import UserRole, username_key
from app.auth.service import normalize_username
from app.auth.sessions import revoke_user_sessions
from app.db.models import User


class UserManagementError(ValueError):
    """Base error for safe user-management failures."""


class UsernameAlreadyExistsError(UserManagementError):
    pass


class UserNotFoundError(UserManagementError):
    pass


class SelfManagementError(UserManagementError):
    pass


class LastActiveAdministratorError(UserManagementError):
    pass


def list_users(session: Session) -> tuple[User, ...]:
    return tuple(session.scalars(select(User).order_by(User.username_key)))


def create_user(
    session: Session,
    username: str,
    temporary_password: str,
    role: UserRole,
    *,
    actor_user_id: int,
) -> User:
    normalized = normalize_username(username)
    password_hash = hash_password(temporary_password)
    _begin_immediate(session)
    if session.scalar(select(User.id).where(User.username_key == username_key(normalized))):
        raise UsernameAlreadyExistsError("Já existe um usuário com esse nome.")
    user = User(
        username=normalized,
        username_key=username_key(normalized),
        password_hash=password_hash,
        role=role.value,
        is_active=True,
        password_change_required=True,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as error:
        raise UsernameAlreadyExistsError("Já existe um usuário com esse nome.") from error
    _audit(session, "USER_CREATED", actor_user_id, user, {"role": role.value})
    return user


def change_user_role(
    session: Session,
    target_user_id: int,
    role: UserRole,
    *,
    actor_user_id: int,
) -> User:
    _begin_immediate(session)
    user = _managed_user(session, target_user_id, actor_user_id)
    if user.role == role.value:
        return user
    if user.role == UserRole.ADMIN.value and role is not UserRole.ADMIN:
        _require_another_active_administrator(session, user)
    previous = user.role
    user.role = role.value
    user.updated_at = datetime.now(UTC)
    revoke_user_sessions(session, user.id)
    _audit(
        session,
        "USER_ROLE_CHANGED",
        actor_user_id,
        user,
        {"previous_role": previous, "role": role.value},
    )
    return user


def set_user_active(
    session: Session,
    target_user_id: int,
    active: bool,
    *,
    actor_user_id: int,
) -> User:
    _begin_immediate(session)
    user = _managed_user(session, target_user_id, actor_user_id)
    if user.is_active == active:
        return user
    if not active and user.role == UserRole.ADMIN.value:
        _require_another_active_administrator(session, user)
    user.is_active = active
    user.updated_at = datetime.now(UTC)
    revoke_user_sessions(session, user.id)
    _audit(
        session,
        "USER_STATUS_CHANGED",
        actor_user_id,
        user,
        {"active": active},
    )
    return user


def reset_user_password(
    session: Session,
    target_user_id: int,
    temporary_password: str,
    *,
    actor_user_id: int,
) -> User:
    password_hash = hash_password(temporary_password)
    _begin_immediate(session)
    user = _managed_user(session, target_user_id, actor_user_id)
    user.password_hash = password_hash
    user.password_change_required = True
    user.updated_at = datetime.now(UTC)
    revoke_user_sessions(session, user.id)
    _audit(session, "USER_PASSWORD_RESET", actor_user_id, user)
    return user


def update_own_password(session: Session, user_id: int, new_password: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError("Usuário não encontrado.")
    user.password_hash = hash_password(new_password)
    user.password_change_required = False
    user.updated_at = datetime.now(UTC)
    revoke_user_sessions(session, user.id)
    session.flush()
    return user


def _managed_user(session: Session, target_user_id: int, actor_user_id: int) -> User:
    if target_user_id == actor_user_id:
        raise SelfManagementError(
            "Use Minha conta para alterar sua própria senha; "
            "papel e status próprios não podem ser alterados."
        )
    user = session.get(User, target_user_id)
    if user is None:
        raise UserNotFoundError("Usuário não encontrado.")
    return user


def _begin_immediate(session: Session) -> None:
    if not session.in_transaction():
        session.execute(text("BEGIN IMMEDIATE"))


def _require_another_active_administrator(session: Session, user: User) -> None:
    active_admins = session.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.ADMIN.value,
            User.is_active.is_(True),
        )
    )
    if user.is_active and active_admins is not None and active_admins <= 1:
        raise LastActiveAdministratorError("O último administrador ativo deve ser preservado.")


def _audit(
    session: Session,
    action: str,
    actor_user_id: int,
    user: User,
    details: dict[str, object] | None = None,
) -> None:
    record_audit_event(
        session,
        occurred_at=datetime.now(UTC),
        action=action,
        result=AUDIT_RESULT_SUCCESS,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=actor_user_id,
        target=user.username,
        details=details,
    )
    session.flush()
