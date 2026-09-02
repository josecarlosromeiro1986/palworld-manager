from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"


def username_key(username: str) -> str:
    return username.casefold()
