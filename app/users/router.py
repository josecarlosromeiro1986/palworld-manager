from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.csrf import tokens_match
from app.auth.passwords import PasswordPolicyError
from app.auth.roles import UserRole
from app.auth.service import InvalidUsernameError
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.db.engine import session_scope
from app.users.service import (
    UserManagementError,
    change_user_role,
    create_user,
    list_users,
    reset_user_password,
    set_user_active,
)

router = APIRouter(prefix="/users")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def _valid_csrf(request: Request, csrf_token: str | None) -> bool:
    principal = _principal(request)
    return tokens_match(
        request.cookies.get(SESSION_CSRF_COOKIE_NAME), csrf_token
    ) and session_csrf_is_valid(principal, csrf_token)


def _page(
    request: Request,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    with session_scope(_session_factory(request)) as session:
        users = list_users(session)
    principal = _principal(request)
    return templates.TemplateResponse(
        request=request,
        name="users/index.html",
        context={
            "username": principal.username,
            "role": principal.role.value,
            "current_user_id": principal.user_id,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "users",
            "users": users,
            "error": error,
            "saved": request.query_params.get("saved"),
        },
        status_code=status_code,
    )


def _error_response(request: Request, error: Exception) -> Response:
    return _page(request, error=str(error), status_code=400)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def users_page(request: Request) -> Response:
    return _page(request)


@router.post("", include_in_schema=False)
def create(
    request: Request,
    username: Annotated[str, Form()],
    temporary_password: Annotated[str, Form()],
    temporary_password_confirmation: Annotated[str, Form()],
    role: Annotated[UserRole, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    if temporary_password != temporary_password_confirmation:
        return _page(
            request, error="A confirmação da senha temporária não confere.", status_code=400
        )
    try:
        with session_scope(_session_factory(request)) as session:
            create_user(
                session,
                username,
                temporary_password,
                role,
                actor_user_id=_principal(request).user_id,
            )
    except (UserManagementError, InvalidUsernameError, PasswordPolicyError) as error:
        return _error_response(request, error)
    return RedirectResponse("/users?saved=created", status_code=303)


@router.post("/{user_id}/role", include_in_schema=False)
def update_role(
    request: Request,
    user_id: int,
    role: Annotated[UserRole, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            change_user_role(session, user_id, role, actor_user_id=_principal(request).user_id)
    except UserManagementError as error:
        return _error_response(request, error)
    return RedirectResponse("/users?saved=role", status_code=303)


@router.post("/{user_id}/status", include_in_schema=False)
def update_status(
    request: Request,
    user_id: int,
    active: Annotated[bool, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            set_user_active(
                session,
                user_id,
                active,
                actor_user_id=_principal(request).user_id,
            )
    except UserManagementError as error:
        return _error_response(request, error)
    return RedirectResponse("/users?saved=status", status_code=303)


@router.post("/{user_id}/password", include_in_schema=False)
def reset_password(
    request: Request,
    user_id: int,
    temporary_password: Annotated[str, Form()],
    temporary_password_confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    if temporary_password != temporary_password_confirmation:
        return _page(
            request, error="A confirmação da senha temporária não confere.", status_code=400
        )
    try:
        with session_scope(_session_factory(request)) as session:
            reset_user_password(
                session,
                user_id,
                temporary_password,
                actor_user_id=_principal(request).user_id,
            )
    except (UserManagementError, PasswordPolicyError) as error:
        return _error_response(request, error)
    return RedirectResponse("/users?saved=password", status_code=303)
