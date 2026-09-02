from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.csrf import tokens_match
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.palworld_settings.service import (
    PalworldSettingsSaveResult,
    PalworldSettingsService,
    PalworldSettingsSnapshot,
    PalworldSettingsValidationError,
)
from app.palworld_settings.storage import PalworldSettingsStorageError

router = APIRouter(prefix="/palworld-settings")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _service(request: Request) -> PalworldSettingsService:
    return cast(PalworldSettingsService, request.app.state.palworld_settings_service)


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
    snapshot: PalworldSettingsSnapshot | None,
    error: str | None = None,
    success: str | None = None,
    save_result: PalworldSettingsSaveResult | None = None,
    submitted_values: dict[str, str] | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="palworld_settings/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "palworld-settings",
            "snapshot": snapshot,
            "error": error,
            "success": success,
            "save_result": save_result,
            "submitted_values": submitted_values or {},
        },
        status_code=status_code,
    )


def _load_response(request: Request) -> Response:
    try:
        snapshot = _service(request).load()
    except PalworldSettingsStorageError as error:
        return _page_response(
            request,
            snapshot=None,
            error=error.public_message,
            status_code=503,
        )
    except PalworldSettingsValidationError as error:
        return _page_response(request, snapshot=None, error=str(error), status_code=422)
    return _page_response(request, snapshot=snapshot)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def settings_page(request: Request) -> Response:
    return _load_response(request)


@router.post("", response_class=HTMLResponse, include_in_schema=False)
async def save_settings(request: Request) -> Response:
    form = await request.form()
    csrf_token = _text_value(form, "csrf_token")
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    expected_version = _text_value(form, "version")
    if expected_version is None:
        return PlainTextResponse("Versão do arquivo ausente.", status_code=400)
    try:
        updates = _setting_values(form)
    except PalworldSettingsValidationError as error:
        return _page_response(
            request,
            snapshot=_safe_snapshot(request),
            error=str(error),
            status_code=400,
        )

    try:
        result = _service(request).save(
            updates,
            expected_version=expected_version,
            administrator_user_id=_principal(request).user_id,
        )
    except PalworldSettingsValidationError as error:
        return _page_response(
            request,
            snapshot=_safe_snapshot(request),
            error=str(error),
            submitted_values=updates,
            status_code=400,
        )
    except PalworldSettingsStorageError as error:
        status_code = 409 if error.kind.value == "conflict" else 503
        return _page_response(
            request,
            snapshot=_safe_snapshot(request),
            error=error.public_message,
            submitted_values=updates,
            status_code=status_code,
        )

    if result.changed_fields:
        success = "Configurações salvas com backup. Reinicie o Palworld para aplicá-las."
    else:
        success = "Nenhuma alteração foi necessária."
    return _page_response(
        request,
        snapshot=result.snapshot,
        success=success,
        save_result=result,
    )


def _safe_snapshot(request: Request) -> PalworldSettingsSnapshot | None:
    try:
        return _service(request).load()
    except (PalworldSettingsStorageError, PalworldSettingsValidationError):
        return None


def _text_value(form: FormData, key: str) -> str | None:
    value = form.get(key)
    return value if isinstance(value, str) else None


def _setting_values(form: FormData) -> dict[str, str]:
    values: dict[str, str] = {}
    for form_key, value in form.multi_items():
        if not form_key.startswith("setting__"):
            continue
        setting_key = form_key.removeprefix("setting__")
        if not setting_key or not isinstance(value, str) or setting_key in values:
            raise PalworldSettingsValidationError("O formulário contém campos inválidos.")
        values[setting_key] = value
    return values
