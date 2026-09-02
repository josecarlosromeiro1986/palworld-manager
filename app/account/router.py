from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.auth.cookies import (
    SESSION_CSRF_COOKIE_NAME,
    clear_authentication_cookies,
)
from app.auth.csrf import tokens_match
from app.auth.login_protection import attempt_user_login
from app.auth.passwords import PasswordPolicyError, validate_password
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.config import Settings
from app.db.engine import session_scope
from app.users.service import update_own_password

router = APIRouter(prefix="/account")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _source_address(request: Request) -> str | None:
    return request.client.host[:45] if request.client is not None else None


def _page(request: Request, *, error: str | None = None, status_code: int = 200) -> Response:
    principal = _principal(request)
    return templates.TemplateResponse(
        request=request,
        name="account/index.html",
        context={
            "username": principal.username,
            "role": principal.role.value,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "account",
            "password_change_required": principal.password_change_required,
            "error": error,
        },
        status_code=status_code,
    )


def _audit_password(
    session: Session,
    user_id: int,
    result: str,
    reason: str | None = None,
) -> None:
    record_audit_event(
        session,
        occurred_at=datetime.now(UTC),
        action="ACCOUNT_PASSWORD_UPDATE",
        result=result,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        target="Conta própria",
        reason=reason,
    )


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def account_page(request: Request) -> Response:
    return _page(request)


@router.post("/password", include_in_schema=False)
def update_password(
    request: Request,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    new_password_confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    principal = _principal(request)
    cookie_token = request.cookies.get(SESSION_CSRF_COOKIE_NAME)
    if not tokens_match(cookie_token, csrf_token) or not session_csrf_is_valid(
        principal, csrf_token
    ):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    if new_password != new_password_confirmation:
        with session_scope(_session_factory(request)) as session:
            _audit_password(
                session, principal.user_id, AUDIT_RESULT_FAILURE, "CONFIRMATION_MISMATCH"
            )
        return _page(request, error="A confirmação da nova senha não confere.", status_code=400)
    try:
        validate_password(new_password)
    except PasswordPolicyError as error:
        with session_scope(_session_factory(request)) as session:
            _audit_password(session, principal.user_id, AUDIT_RESULT_FAILURE, "POLICY_REJECTED")
        return _page(request, error=str(error), status_code=400)

    with session_scope(_session_factory(request)) as session:
        authentication = attempt_user_login(
            session,
            principal.username,
            current_password,
            _source_address(request),
        )
        if authentication.user is None:
            _audit_password(
                session,
                principal.user_id,
                AUDIT_RESULT_FAILURE,
                "CURRENT_PASSWORD_REJECTED",
            )
        else:
            update_own_password(session, principal.user_id, new_password)
            _audit_password(session, principal.user_id, AUDIT_RESULT_SUCCESS)
    if authentication.user is None:
        message = (
            "Muitas tentativas. Tente novamente mais tarde."
            if authentication.blocked
            else "A senha atual não confere."
        )
        return _page(request, error=message, status_code=429 if authentication.blocked else 400)

    response = RedirectResponse("/login?password_changed=1", status_code=303)
    clear_authentication_cookies(response, _settings(request))
    return response
