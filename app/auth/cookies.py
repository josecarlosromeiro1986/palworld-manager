from fastapi import Response

from app.auth.csrf import generate_token
from app.auth.sessions import SESSION_MAXIMUM_DURATION, IssuedSession
from app.config import AppEnvironment, Settings

SESSION_COOKIE_NAME = "palworld_manager_session"
SESSION_CSRF_COOKIE_NAME = "palworld_manager_csrf"
LOGIN_CSRF_COOKIE_NAME = "palworld_manager_login_csrf"
LOGIN_CSRF_MAX_AGE_SECONDS = 10 * 60
SESSION_MAX_AGE_SECONDS = int(SESSION_MAXIMUM_DURATION.total_seconds())


def cookies_are_secure(settings: Settings) -> bool:
    return settings.environment is AppEnvironment.PRODUCTION


def new_login_csrf_token() -> str:
    return generate_token()


def set_login_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        LOGIN_CSRF_COOKIE_NAME,
        token,
        max_age=LOGIN_CSRF_MAX_AGE_SECONDS,
        secure=cookies_are_secure(settings),
        httponly=True,
        samesite="strict",
        path="/",
    )


def set_session_cookies(response: Response, issued: IssuedSession, settings: Settings) -> None:
    for name, value in (
        (SESSION_COOKIE_NAME, issued.session_token),
        (SESSION_CSRF_COOKIE_NAME, issued.csrf_token),
    ):
        response.set_cookie(
            name,
            value,
            max_age=SESSION_MAX_AGE_SECONDS,
            secure=cookies_are_secure(settings),
            httponly=True,
            samesite="strict",
            path="/",
        )


def clear_login_csrf_cookie(response: Response, settings: Settings) -> None:
    _clear_cookie(response, LOGIN_CSRF_COOKIE_NAME, settings)


def clear_authentication_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE_NAME, SESSION_CSRF_COOKIE_NAME, LOGIN_CSRF_COOKIE_NAME):
        _clear_cookie(response, name, settings)


def _clear_cookie(response: Response, name: str, settings: Settings) -> None:
    response.set_cookie(
        name,
        "",
        max_age=0,
        expires=0,
        secure=cookies_are_secure(settings),
        httponly=True,
        samesite="strict",
        path="/",
    )
