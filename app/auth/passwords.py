from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

MINIMUM_PASSWORD_LENGTH = 6

_password_hasher = PasswordHasher(type=Type.ID)


class PasswordTooShortError(ValueError):
    """Raised when a password does not meet the minimum length."""


def validate_password(password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise PasswordTooShortError(
            f"A senha deve ter no minimo {MINIMUM_PASSWORD_LENGTH} caracteres."
        )


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False
