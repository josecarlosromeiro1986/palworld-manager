import pytest

from app.auth.passwords import (
    PasswordTooShortError,
    hash_password,
    verify_password,
)


def test_password_is_stored_as_argon2id_hash() -> None:
    password = "senha-ficticia-segura"

    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash
    assert verify_password(password, password_hash)


def test_password_verification_rejects_wrong_password_and_invalid_hash() -> None:
    password_hash = hash_password("senha-correta")

    assert not verify_password("senha-incorreta", password_hash)
    assert not verify_password("senha-correta", "hash-invalido")


def test_password_requires_at_least_six_characters() -> None:
    with pytest.raises(PasswordTooShortError, match="6 caracteres"):
        hash_password("12345")
