from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.csrf import tokens_match
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.db.engine import session_scope
from app.integrations.palworld_rest import PalworldRestClient, PalworldRestError
from app.players.administration import (
    PlayerAction,
    PlayerAdministrationService,
    PlayerAdministrationValidationError,
)
from app.players.service import ManualPlayersService

router = APIRouter(prefix="/players")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _players_service(request: Request) -> ManualPlayersService:
    return cast(ManualPlayersService, request.app.state.players_service)


def _rest_client(request: Request) -> PalworldRestClient:
    return cast(PalworldRestClient, request.app.state.palworld_rest_client)


def _administration_service(request: Request) -> PlayerAdministrationService:
    return cast(
        PlayerAdministrationService,
        request.app.state.player_administration_service,
    )


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _valid_session_csrf(request: Request, csrf_token: str | None) -> bool:
    principal = _principal(request)
    cookie_token = request.cookies.get(SESSION_CSRF_COOKIE_NAME)
    return tokens_match(cookie_token, csrf_token) and session_csrf_is_valid(
        principal,
        csrf_token,
    )


def _page_response(
    request: Request,
    *,
    players_error: str | None = None,
    announcement_error: str | None = None,
    announcement_success: str | None = None,
    announcement_message: str = "",
    administration_error: str | None = None,
    administration_success: str | None = None,
    unban_user_id: str = "",
    unban_reason: str = "",
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="players/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "players",
            "snapshot": _players_service(request).cached(),
            "players_error": players_error,
            "announcement_error": announcement_error,
            "announcement_success": announcement_success,
            "announcement_message": announcement_message,
            "administration_error": administration_error,
            "administration_success": administration_success,
            "unban_user_id": unban_user_id,
            "unban_reason": unban_reason,
            "administration_history": _administration_service(request).history(),
        },
        status_code=status_code,
    )


def _audit_announcement(
    request: Request,
    *,
    message: str,
    result: str,
    error_kind: str | None = None,
) -> None:
    details: dict[str, object] = {"message": message}
    if error_kind is not None:
        details["error_kind"] = error_kind
    with session_scope(_session_factory(request)) as session:
        record_audit_event(
            session,
            occurred_at=datetime.now(UTC),
            action="PALWORLD_ANNOUNCEMENT",
            result=result,
            origin=AUDIT_ORIGIN_ADMINISTRATOR,
            user_id=_principal(request).user_id,
            target="Jogadores online",
            details=details,
        )


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def players_page(request: Request) -> Response:
    return _page_response(request)


@router.post("/refresh", response_class=HTMLResponse, include_in_schema=False)
def refresh_players(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        _players_service(request).refresh()
    except PalworldRestError as error:
        return _page_response(request, players_error=error.public_message, status_code=503)
    return _page_response(request)


def _cached_target(request: Request, user_id: str) -> str:
    snapshot = _players_service(request).cached()
    if snapshot is not None:
        for player in snapshot.players:
            if player.user_id == user_id:
                return player.name
    return user_id


def _execute_player_action(
    request: Request,
    *,
    action: PlayerAction,
    user_id: str,
    reason: str,
    csrf_token: str | None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    target = _cached_target(request, user_id) if action is not PlayerAction.UNBAN else user_id
    try:
        _administration_service(request).execute(
            action,
            user_id=user_id,
            target=target,
            reason=reason,
            administrator_user_id=_principal(request).user_id,
        )
    except PlayerAdministrationValidationError as error:
        return _page_response(
            request,
            administration_error=str(error),
            unban_user_id=user_id if action is PlayerAction.UNBAN else "",
            unban_reason=reason if action is PlayerAction.UNBAN else "",
            status_code=400,
        )
    except PalworldRestError as error:
        return _page_response(
            request,
            administration_error=error.public_message,
            unban_user_id=user_id if action is PlayerAction.UNBAN else "",
            unban_reason=reason if action is PlayerAction.UNBAN else "",
            status_code=503,
        )
    labels = {
        PlayerAction.KICK: "Kick executado e registrado com sucesso.",
        PlayerAction.BAN: "Ban executado e registrado com sucesso.",
        PlayerAction.UNBAN: "Unban executado e registrado com sucesso.",
    }
    return _page_response(request, administration_success=labels[action])


@router.post("/kick", response_class=HTMLResponse, include_in_schema=False)
def kick_player(
    request: Request,
    user_id: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _execute_player_action(
        request,
        action=PlayerAction.KICK,
        user_id=user_id,
        reason=reason,
        csrf_token=csrf_token,
    )


@router.post("/ban", response_class=HTMLResponse, include_in_schema=False)
def ban_player(
    request: Request,
    user_id: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _execute_player_action(
        request,
        action=PlayerAction.BAN,
        user_id=user_id,
        reason=reason,
        csrf_token=csrf_token,
    )


@router.post("/unban", response_class=HTMLResponse, include_in_schema=False)
def unban_player(
    request: Request,
    user_id: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _execute_player_action(
        request,
        action=PlayerAction.UNBAN,
        user_id=user_id,
        reason=reason,
        csrf_token=csrf_token,
    )


@router.post("/announce", response_class=HTMLResponse, include_in_schema=False)
def announce(
    request: Request,
    message: Annotated[str, Form()],
    confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    if not message.strip():
        _audit_announcement(request, message=message, result=AUDIT_RESULT_FAILURE)
        return _page_response(
            request,
            announcement_error="A mensagem do anúncio é obrigatória.",
            announcement_message=message,
            status_code=400,
        )
    if confirmation != message:
        _audit_announcement(request, message=message, result=AUDIT_RESULT_FAILURE)
        return _page_response(
            request,
            announcement_error="A confirmação deve repetir exatamente a mensagem.",
            announcement_message=message,
            status_code=400,
        )
    try:
        _rest_client(request).announce(message)
    except PalworldRestError as error:
        _audit_announcement(
            request,
            message=message,
            result=AUDIT_RESULT_FAILURE,
            error_kind=error.kind.value,
        )
        return _page_response(
            request,
            announcement_error=error.public_message,
            announcement_message=message,
            status_code=503,
        )
    _audit_announcement(request, message=message, result=AUDIT_RESULT_SUCCESS)
    return _page_response(
        request,
        announcement_success="Anúncio enviado e auditado com sucesso.",
    )
