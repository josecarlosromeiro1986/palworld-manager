from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.auth.cookies import (
    LOGIN_CSRF_COOKIE_NAME,
    SESSION_CSRF_COOKIE_NAME,
    clear_authentication_cookies,
    clear_login_csrf_cookie,
    new_login_csrf_token,
    set_login_csrf_cookie,
    set_session_cookies,
)
from app.auth.csrf import tokens_match
from app.auth.login_protection import attempt_administrator_login
from app.auth.sessions import (
    SessionPrincipal,
    issue_session,
    revoke_session,
    session_csrf_is_valid,
)
from app.config import Settings
from app.db.engine import session_scope

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _source_address(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host[:45]


def _login_page(
    request: Request,
    csrf_token: str,
    *,
    status_code: int = 200,
    error: str | None = None,
    username: str = "",
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"csrf_token": csrf_token, "error": error, "username": username},
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request) -> Response:
    csrf_token = new_login_csrf_token()
    response = _login_page(request, csrf_token)
    set_login_csrf_cookie(response, csrf_token, _settings(request))
    return response


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    login_csrf_cookie = request.cookies.get(LOGIN_CSRF_COOKIE_NAME)
    if (
        login_csrf_cookie is None
        or csrf_token is None
        or not tokens_match(login_csrf_cookie, csrf_token)
    ):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)

    with session_scope(_session_factory(request)) as session:
        result = attempt_administrator_login(
            session,
            username,
            password,
            _source_address(request),
        )
        if result.blocked:
            return _login_page(
                request,
                login_csrf_cookie,
                status_code=429,
                error="Muitas tentativas. Tente novamente mais tarde.",
                username=username,
            )
        if result.user is None:
            return _login_page(
                request,
                login_csrf_cookie,
                status_code=401,
                error="Usuário ou senha inválidos.",
                username=username,
            )
        issued = issue_session(session, result.user)

    response = RedirectResponse("/", status_code=303)
    set_session_cookies(response, issued, _settings(request))
    clear_login_csrf_cookie(response, _settings(request))
    return response


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request) -> Response:
    principal = _principal(request)
    csrf_token = request.cookies.get(SESSION_CSRF_COOKIE_NAME)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"username": principal.username, "csrf_token": csrf_token},
    )


@router.post("/logout", include_in_schema=False)
def logout(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    principal = _principal(request)
    cookie_token = request.cookies.get(SESSION_CSRF_COOKIE_NAME)
    if not tokens_match(cookie_token, csrf_token) or not session_csrf_is_valid(
        principal, csrf_token
    ):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)

    with session_scope(_session_factory(request)) as session:
        revoke_session(session, principal.session_id)

    response = RedirectResponse("/login", status_code=303)
    clear_authentication_cookies(response, _settings(request))
    return response
