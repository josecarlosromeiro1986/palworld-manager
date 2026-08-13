from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import revoke_user_sessions
from app.db.models import User

MAXIMUM_USERNAME_LENGTH = 100


class AdministratorAlreadyExistsError(ValueError):
    """Raised when the single V1 administrator already exists."""


class AdministratorNotFoundError(ValueError):
    """Raised when a requested administrator does not exist."""


class InvalidUsernameError(ValueError):
    """Raised when an administrator username is invalid."""


def normalize_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise InvalidUsernameError("O nome de usuario nao pode ficar vazio.")
    if len(normalized) > MAXIMUM_USERNAME_LENGTH:
        raise InvalidUsernameError(
            f"O nome de usuario deve ter no maximo {MAXIMUM_USERNAME_LENGTH} caracteres."
        )
    return normalized


def create_administrator(session: Session, username: str, password: str) -> User:
    if session.scalar(select(User.id).limit(1)) is not None:
        raise AdministratorAlreadyExistsError("O administrador inicial ja foi criado.")

    administrator = User(
        username=normalize_username(username),
        password_hash=hash_password(password),
    )
    session.add(administrator)
    session.flush()
    return administrator


def authenticate_administrator(session: Session, username: str, password: str) -> User | None:
    try:
        normalized_username = normalize_username(username)
    except InvalidUsernameError:
        return None

    administrator = session.scalar(
        select(User).where(
            User.username == normalized_username,
            User.is_active.is_(True),
        )
    )
    if administrator is None or not verify_password(password, administrator.password_hash):
        return None
    return administrator


def reset_administrator_password(session: Session, username: str, password: str) -> User:
    normalized_username = normalize_username(username)
    administrator = session.scalar(select(User).where(User.username == normalized_username))
    if administrator is None:
        raise AdministratorNotFoundError("Administrador nao encontrado.")

    administrator.password_hash = hash_password(password)
    administrator.updated_at = datetime.now(UTC)
    revoke_user_sessions(session, administrator.id)
    session.flush()
    return administrator
