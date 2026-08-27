from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

MINIMUM_PASSWORD_LENGTH = 6
MAXIMUM_PASSWORD_LENGTH = 1024

_password_hasher = PasswordHasher(type=Type.ID)
_dummy_password_hash = _password_hasher.hash("palworld-manager-dummy-password")


class PasswordPolicyError(ValueError):
    """Raised when a password does not satisfy the local policy."""


class PasswordTooShortError(PasswordPolicyError):
    """Raised when a password does not meet the minimum length."""


class PasswordTooLongError(PasswordPolicyError):
    """Raised when a password exceeds the defensive input limit."""


def validate_password(password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise PasswordTooShortError(
            f"A senha deve ter no minimo {MINIMUM_PASSWORD_LENGTH} caracteres."
        )
    if len(password) > MAXIMUM_PASSWORD_LENGTH:
        raise PasswordTooLongError(
            f"A senha deve ter no maximo {MAXIMUM_PASSWORD_LENGTH} caracteres."
        )


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if len(password) > MAXIMUM_PASSWORD_LENGTH:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash if password_hash is not None else _dummy_password_hash
    verified = verify_password(password, candidate_hash)
    return password_hash is not None and verified
