from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import FormData
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
from app.auth.login_protection import attempt_administrator_login
from app.auth.passwords import PasswordTooShortError, validate_password
from app.auth.service import reset_administrator_password
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.backups.drive_jobs import (
    DRIVE_CHECK_JOB_KIND,
    DriveJobConflictError,
    drive_job_view,
    enqueue_drive_check,
)
from app.config import Settings
from app.db.engine import session_scope
from app.db.models import Job, NotificationEvent
from app.manager_settings.service import (
    OPERATIONAL_SETTING_KEYS,
    ManagerSettingsConflictError,
    ManagerSettingsValidationError,
    audit_manager_settings_failure,
    enqueue_discord_test,
    latest_discord_test,
    load_manager_settings,
    update_manager_settings,
    validate_manager_settings,
)
from app.notifications.service import DISCORD_TEST, NOTIFICATION_CHANNEL_DISCORD

router = APIRouter(prefix="/manager-settings")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
OPERATIONAL_FORM_FIELDS = OPERATIONAL_SETTING_KEYS | {"csrf_token", "settings_version"}


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _source_address(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host[:45]


def _valid_session_csrf(request: Request, csrf_token: str | None) -> bool:
    principal = _principal(request)
    cookie_token = request.cookies.get(SESSION_CSRF_COOKIE_NAME)
    return tokens_match(cookie_token, csrf_token) and session_csrf_is_valid(
        principal,
        csrf_token,
    )


def _latest_drive_test(session: Session) -> Job | None:
    return session.scalar(
        select(Job).where(Job.kind == DRIVE_CHECK_JOB_KIND).order_by(Job.id.desc()).limit(1)
    )


def _page_response(
    request: Request,
    *,
    status_code: int = 200,
    operational_error: str | None = None,
    operational_saved: bool = False,
    password_error: str | None = None,
    password_changed: bool = False,
) -> Response:
    with session_scope(_session_factory(request)) as session:
        snapshot = load_manager_settings(session)
        discord_test = latest_discord_test(session)
        drive_test = _latest_drive_test(session)
    return templates.TemplateResponse(
        request=request,
        name="manager_settings/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "manager-settings",
            "manager_settings": snapshot.values,
            "settings_version": snapshot.version,
            "operational_error": operational_error,
            "operational_saved": operational_saved,
            "password_error": password_error,
            "password_changed": password_changed,
            "discord_test": discord_test,
            "drive_test": drive_job_view(drive_test) if drive_test is not None else None,
        },
        status_code=status_code,
    )


def _discord_test_response(request: Request, event: NotificationEvent) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="manager_settings/_discord_test.html",
        context={"discord_test": event},
    )


def _drive_test_response(request: Request, job: Job) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="manager_settings/_drive_test.html",
        context={"drive_test": drive_job_view(job)},
    )


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def manager_settings_page(request: Request) -> Response:
    return _page_response(request)


def _single_form_value(form: FormData, key: str) -> str:
    values = form.getlist(key)
    if len(values) != 1 or not isinstance(values[0], str):
        raise ManagerSettingsValidationError("Formulário inválido.")
    return values[0]


def _integer_form_value(form: FormData, key: str) -> int:
    raw = _single_form_value(form, key)
    try:
        return int(raw)
    except ValueError as error:
        raise ManagerSettingsValidationError("Formulário inválido.") from error


def _parse_operational_form(form: FormData) -> tuple[dict[str, object], str]:
    if set(form.keys()) - OPERATIONAL_FORM_FIELDS:
        raise ManagerSettingsValidationError("Campo não permitido.")
    backup_values = form.getlist("backup_enabled")
    if len(backup_values) > 1 or (
        backup_values and (not isinstance(backup_values[0], str) or backup_values[0] != "true")
    ):
        raise ManagerSettingsValidationError("Formulário inválido.")
    values: dict[str, object] = {
        "timezone": _single_form_value(form, "timezone"),
        "backup_enabled": bool(backup_values),
        "backup_time": _single_form_value(form, "backup_time"),
        "local_backup_retention": _integer_form_value(form, "local_backup_retention"),
        "drive_backup_retention": _integer_form_value(form, "drive_backup_retention"),
        "metrics_interval_seconds": _integer_form_value(form, "metrics_interval_seconds"),
        "assisted_shutdown_default_minutes": _integer_form_value(
            form, "assisted_shutdown_default_minutes"
        ),
        "start_timeout_seconds": _integer_form_value(form, "start_timeout_seconds"),
        "restart_timeout_seconds": _integer_form_value(form, "restart_timeout_seconds"),
        "stop_timeout_seconds": _integer_form_value(form, "stop_timeout_seconds"),
        "disk_warning_gb": _integer_form_value(form, "disk_warning_gb"),
        "disk_critical_gb": _integer_form_value(form, "disk_critical_gb"),
    }
    return values, _single_form_value(form, "settings_version")


@router.post("/operational", response_class=HTMLResponse, include_in_schema=False)
async def update_operational_settings(request: Request) -> Response:
    form = await request.form()
    raw_csrf = form.get("csrf_token")
    csrf_token = raw_csrf if isinstance(raw_csrf, str) else None
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        raw_values, expected_version = _parse_operational_form(form)
        values = validate_manager_settings(raw_values)
    except ManagerSettingsValidationError as error:
        with session_scope(_session_factory(request)) as session:
            audit_manager_settings_failure(
                session,
                user_id=_principal(request).user_id,
                reason="VALIDATION_FAILED",
            )
        return _page_response(request, status_code=400, operational_error=str(error))

    try:
        with session_scope(_session_factory(request)) as session:
            update_manager_settings(
                session,
                values,
                expected_version=expected_version,
                user_id=_principal(request).user_id,
            )
    except ManagerSettingsConflictError as error:
        with session_scope(_session_factory(request)) as session:
            audit_manager_settings_failure(
                session,
                user_id=_principal(request).user_id,
                reason="CONCURRENT_UPDATE",
            )
        return _page_response(request, status_code=409, operational_error=str(error))
    return _page_response(request, operational_saved=True)


def _audit_password_update(
    session: Session,
    *,
    user_id: int,
    result: str,
    reason: str | None = None,
) -> None:
    record_audit_event(
        session,
        occurred_at=datetime.now(UTC),
        action="MANAGER_PASSWORD_UPDATE",
        result=result,
        origin=AUDIT_ORIGIN_ADMINISTRATOR,
        user_id=user_id,
        target="Administrador",
        reason=reason,
    )


@router.post("/password", include_in_schema=False)
def update_password(
    request: Request,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    new_password_confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    principal = _principal(request)
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    if new_password != new_password_confirmation:
        with session_scope(_session_factory(request)) as session:
            _audit_password_update(
                session,
                user_id=principal.user_id,
                result=AUDIT_RESULT_FAILURE,
                reason="CONFIRMATION_MISMATCH",
            )
        return _page_response(
            request,
            status_code=400,
            password_error="A confirmação da nova senha não confere.",
        )
    try:
        validate_password(new_password)
    except PasswordTooShortError as error:
        with session_scope(_session_factory(request)) as session:
            _audit_password_update(
                session,
                user_id=principal.user_id,
                result=AUDIT_RESULT_FAILURE,
                reason="POLICY_REJECTED",
            )
        return _page_response(request, status_code=400, password_error=str(error))

    with session_scope(_session_factory(request)) as session:
        authentication = attempt_administrator_login(
            session,
            principal.username,
            current_password,
            _source_address(request),
        )
        if authentication.user is None:
            _audit_password_update(
                session,
                user_id=principal.user_id,
                result=AUDIT_RESULT_FAILURE,
                reason="CURRENT_PASSWORD_REJECTED",
            )
            blocked = authentication.blocked
        else:
            reset_administrator_password(session, principal.username, new_password)
            _audit_password_update(
                session,
                user_id=principal.user_id,
                result=AUDIT_RESULT_SUCCESS,
            )
            blocked = False
    if authentication.user is None:
        return _page_response(
            request,
            status_code=429 if blocked else 400,
            password_error=(
                "Muitas tentativas. Tente novamente mais tarde."
                if blocked
                else "A senha atual não confere."
            ),
        )

    response = RedirectResponse("/login?password_changed=1", status_code=303)
    clear_authentication_cookies(response, _settings(request))
    return response


@router.post("/discord-test", response_class=HTMLResponse, include_in_schema=False)
def request_discord_test(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    with session_scope(_session_factory(request)) as session:
        event = enqueue_discord_test(
            session,
            user_id=_principal(request).user_id,
        )
        return _discord_test_response(request, event)


@router.get(
    "/discord-tests/{event_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def discord_test_status(request: Request, event_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        event = session.get(NotificationEvent, event_id)
        if (
            event is None
            or event.event_type != DISCORD_TEST
            or event.channel != NOTIFICATION_CHANNEL_DISCORD
        ):
            raise HTTPException(status_code=404, detail="Teste Discord não encontrado.")
        return _discord_test_response(request, event)


@router.post("/drive-test", response_class=HTMLResponse, include_in_schema=False)
def request_drive_test(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_drive_check(
                session,
                user_id=_principal(request).user_id,
            )
    except DriveJobConflictError:
        with session_scope(_session_factory(request)) as session:
            active_job = _latest_drive_test(session)
            if active_job is None:
                raise HTTPException(
                    status_code=409, detail="Teste do Drive em andamento."
                ) from None
            job = active_job
    return _drive_test_response(request, job)


@router.get(
    "/drive-tests/{job_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def drive_test_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None or job.kind != DRIVE_CHECK_JOB_KIND:
            raise HTTPException(status_code=404, detail="Teste do Drive não encontrado.")
        return _drive_test_response(request, job)
