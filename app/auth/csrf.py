import hashlib
import secrets

TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches_hash(token: str | None, expected_hash: str) -> bool:
    if token is None:
        return False
    return secrets.compare_digest(hash_token(token), expected_hash)


def tokens_match(first: str | None, second: str | None) -> bool:
    if first is None or second is None:
        return False
    return secrets.compare_digest(first, second)
