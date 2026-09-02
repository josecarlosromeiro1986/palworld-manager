import re

from app.auth.roles import UserRole
from app.auth.sessions import SessionPrincipal

_USER_GET_PATHS = frozenset(
    {
        "/",
        "/account",
        "/backups/drive/status",
        "/dashboard/active-job",
        "/dashboard/metrics",
        "/dashboard/palworld-health",
        "/dashboard/worker-health",
    }
)
_USER_POST_PATHS = frozenset(
    {
        "/account/password",
        "/dashboard/lifecycle/START",
        "/dashboard/lifecycle/RESTART",
        "/dashboard/shutdown",
        "/logout",
    }
)
_USER_GET_PATTERNS = (
    re.compile(r"/dashboard/lifecycle/jobs/[0-9]+"),
    re.compile(r"/dashboard/shutdown/jobs/[0-9]+"),
)
_USER_POST_PATTERNS = (re.compile(r"/dashboard/shutdown/jobs/[0-9]+/(cancel|now)"),)


def request_is_authorized(principal: SessionPrincipal, method: str, path: str) -> bool:
    if principal.role is UserRole.ADMIN:
        return True
    if method in {"GET", "HEAD"}:
        return path in _USER_GET_PATHS or any(
            pattern.fullmatch(path) for pattern in _USER_GET_PATTERNS
        )
    if method == "POST":
        return path in _USER_POST_PATHS or any(
            pattern.fullmatch(path) for pattern in _USER_POST_PATTERNS
        )
    return False


def password_change_path_is_allowed(method: str, path: str) -> bool:
    return (method in {"GET", "HEAD"} and path == "/account") or (
        method == "POST" and path in {"/account/password", "/logout"}
    )
