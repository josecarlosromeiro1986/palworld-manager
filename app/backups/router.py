from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Annotated, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.csrf import tokens_match
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.backups.jobs import (
    BackupJobConflictError,
    backup_job_view,
    enqueue_local_backup,
    latest_backup_job,
    request_backup_cancel,
)
from app.backups.scheduler import DEFAULT_TIMEZONE, TIMEZONE_KEY
from app.db.engine import session_scope
from app.db.models import AppSetting, BackupRecord, Job
from app.jobs.logs import JobLogStore
from app.jobs.service import TERMINAL_JOB_STATUSES
from app.restores.jobs import (
    LOCAL_RESTORE_JOB_KIND,
    RestoreJobConflictError,
    RestoreRequestError,
    enqueue_local_restore,
    latest_restore_job,
    restore_job_view,
)

router = APIRouter(prefix="/backups")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@dataclass(frozen=True, slots=True)
class BackupListItem:
    id: int
    filename: str
    status: str
    sha256: str
    size: str
    created_at: str
    job_id: int | None
    job_status: str
    uploaded_to_drive: bool


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _job_logs(request: Request) -> JobLogStore:
    return cast(JobLogStore, request.app.state.job_log_store)


def _valid_session_csrf(request: Request, csrf_token: str | None) -> bool:
    principal = _principal(request)
    cookie_token = request.cookies.get(SESSION_CSRF_COOKIE_NAME)
    return tokens_match(cookie_token, csrf_token) and session_csrf_is_valid(
        principal,
        csrf_token,
    )


def _backup_job_response(
    request: Request,
    job: Job | None,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    lines: tuple[str, ...] = ()
    if job is not None:
        try:
            lines = _job_logs(request).tail(job.log_path)
        except (OSError, ValueError):
            lines = ()
    response = templates.TemplateResponse(
        request=request,
        name="backups/_job.html",
        context={
            "job": backup_job_view(job) if job is not None else None,
            "job_log_lines": lines,
            "error": error,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
        },
        status_code=status_code,
    )
    if job is not None and job.status in TERMINAL_JOB_STATUSES:
        response.headers["HX-Trigger"] = "localBackupFinished"
    return response


def _backup_list_response(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        items = _backup_list_items(session)
    return templates.TemplateResponse(
        request=request,
        name="backups/_list.html",
        context={
            "backups": items,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
        },
    )


def _restore_job_response(
    request: Request,
    job: Job | None,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    lines: tuple[str, ...] = ()
    if job is not None:
        try:
            lines = _job_logs(request).tail(job.log_path)
        except (OSError, ValueError):
            lines = ()
    response = templates.TemplateResponse(
        request=request,
        name="restores/_job.html",
        context={
            "restore_job": restore_job_view(job) if job is not None else None,
            "restore_job_log_lines": lines,
            "restore_error": error,
        },
        status_code=status_code,
    )
    if job is not None and job.status in TERMINAL_JOB_STATUSES:
        response.headers["HX-Trigger"] = "localBackupFinished"
    return response


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def backups_page(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        items = _backup_list_items(session)
        latest = latest_backup_job(session)
        latest_view = backup_job_view(latest) if latest is not None else None
        latest_lines = (
            _job_logs(request).tail(latest.log_path)
            if latest is not None and latest.log_path
            else ()
        )
        latest_restore = latest_restore_job(session)
        latest_restore_view = (
            restore_job_view(latest_restore) if latest_restore is not None else None
        )
        latest_restore_lines = (
            _job_logs(request).tail(latest_restore.log_path)
            if latest_restore is not None and latest_restore.log_path
            else ()
        )
    return templates.TemplateResponse(
        request=request,
        name="backups/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "backups",
            "backups": items,
            "job": latest_view,
            "job_log_lines": latest_lines,
            "error": None,
            "restore_job": latest_restore_view,
            "restore_job_log_lines": latest_restore_lines,
            "restore_error": None,
        },
    )


@router.get("/list", response_class=HTMLResponse, include_in_schema=False)
def backup_list(request: Request) -> Response:
    return _backup_list_response(request)


@router.post("", response_class=HTMLResponse, include_in_schema=False)
def request_backup(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_local_backup(
                session,
                user_id=_principal(request).user_id,
                trigger="MANUAL",
            )
            return _backup_job_response(request, job, status_code=202)
    except BackupJobConflictError:
        with session_scope(_session_factory(request)) as session:
            return _backup_job_response(
                request,
                latest_backup_job(session),
                error="Já existe um backup local em andamento.",
                status_code=409,
            )


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def backup_job_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404)
        try:
            return _backup_job_response(request, job)
        except ValueError as error:
            raise HTTPException(status_code=404) from error


@router.post("/jobs/{job_id}/cancel", response_class=HTMLResponse, include_in_schema=False)
def cancel_backup(
    request: Request,
    job_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    with session_scope(_session_factory(request)) as session:
        if not request_backup_cancel(session, job_id, user_id=_principal(request).user_id):
            return PlainTextResponse("O job não pode mais ser cancelado.", status_code=409)
        return _backup_job_response(request, session.get_one(Job, job_id))


@router.post("/{backup_record_id}/restore", response_class=HTMLResponse, include_in_schema=False)
def request_restore(
    request: Request,
    backup_record_id: int,
    confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_local_restore(
                session,
                backup_record_id=backup_record_id,
                confirmation=confirmation,
                user_id=_principal(request).user_id,
            )
            return _restore_job_response(request, job, status_code=202)
    except RestoreRequestError as error:
        return _restore_job_response(request, None, error=str(error), status_code=400)
    except RestoreJobConflictError:
        with session_scope(_session_factory(request)) as session:
            return _restore_job_response(
                request,
                latest_restore_job(session),
                error="Já existe um Restore local em andamento.",
                status_code=409,
            )


@router.get("/restore/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def restore_job_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None or job.kind != LOCAL_RESTORE_JOB_KIND:
            raise HTTPException(status_code=404)
        return _restore_job_response(request, job)


def _configured_timezone(session: Session) -> ZoneInfo:
    setting = session.get(AppSetting, TIMEZONE_KEY)
    value = (
        setting.value
        if setting is not None and isinstance(setting.value, str)
        else DEFAULT_TIMEZONE
    )
    try:
        return ZoneInfo(value)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _backup_list_items(session: Session) -> tuple[BackupListItem, ...]:
    timezone = _configured_timezone(session)
    records = tuple(
        session.scalars(
            select(BackupRecord)
            .where(BackupRecord.location == "LOCAL", BackupRecord.status == "VALID")
            .order_by(BackupRecord.created_at.desc())
        )
    )
    jobs = {
        job.id: job
        for job in session.scalars(
            select(Job).where(Job.id.in_([record.job_id for record in records if record.job_id]))
        )
    }
    remote_names = set(
        session.scalars(
            select(BackupRecord.filename).where(
                BackupRecord.location == "DRIVE", BackupRecord.status == "VALID"
            )
        )
    )
    return tuple(
        _list_item(
            record,
            jobs.get(record.job_id) if record.job_id is not None else None,
            timezone,
            uploaded_to_drive=record.filename in remote_names,
        )
        for record in records
    )


def _list_item(
    record: BackupRecord,
    job: Job | None,
    timezone: ZoneInfo,
    *,
    uploaded_to_drive: bool,
) -> BackupListItem:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return BackupListItem(
        id=record.id,
        filename=record.filename,
        status=record.status,
        sha256=record.sha256 or "indisponível",
        size=_format_bytes(record.size_bytes or 0),
        created_at=created_at.astimezone(timezone).strftime("%d/%m/%Y %H:%M:%S %Z"),
        job_id=record.job_id,
        job_status=job.status if job is not None else "SEM JOB",
        uploaded_to_drive=uploaded_to_drive,
    )


def _format_bytes(value: int) -> str:
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")
