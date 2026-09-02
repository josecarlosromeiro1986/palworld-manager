from collections.abc import Callable
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
from app.backups.drive_jobs import (
    DRIVE_JOB_KINDS,
    DriveJobConflictError,
    DriveJobRequestError,
    drive_job_view,
    enqueue_drive_check,
    enqueue_drive_delete,
    enqueue_drive_download,
    enqueue_drive_upload,
    latest_drive_job,
    request_drive_cancel,
)
from app.backups.scheduler import DEFAULT_TIMEZONE, TIMEZONE_KEY
from app.db.engine import session_scope
from app.db.models import AppSetting, BackupRecord, Job
from app.jobs.logs import JobLogStore
from app.jobs.service import TERMINAL_JOB_STATUSES

router = APIRouter(prefix="/backups/drive")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@dataclass(frozen=True, slots=True)
class RemoteBackupListItem:
    id: int
    filename: str
    status: str
    sha256: str
    size: str
    created_at: str
    job_id: int | None
    job_status: str
    available_locally: bool


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


def _job_response(
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
        name="backups/_drive_job.html",
        context={
            "drive_job": drive_job_view(job) if job is not None else None,
            "drive_job_log_lines": lines,
            "drive_error": error,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
        },
        status_code=status_code,
    )
    if job is not None and job.status in TERMINAL_JOB_STATUSES:
        response.headers["HX-Trigger"] = "driveBackupsChanged, localBackupFinished"
    return response


@router.get("/list", response_class=HTMLResponse, include_in_schema=False)
def drive_list(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        items = _remote_list_items(session)
        latest = latest_drive_job(session)
        latest_view = drive_job_view(latest) if latest is not None else None
    return templates.TemplateResponse(
        request=request,
        name="backups/_drive_list.html",
        context={
            "remote_backups": items,
            "drive_status": latest_view,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
        },
    )


@router.get("/latest", response_class=HTMLResponse, include_in_schema=False)
def latest_drive_status(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        return _job_response(request, latest_drive_job(session))


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
def drive_status(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        latest = latest_drive_job(session)
        view = drive_job_view(latest) if latest is not None else None
    return templates.TemplateResponse(
        request=request,
        name="dashboard/_drive_status.html",
        context={"drive_status": view},
    )


@router.post("/check", response_class=HTMLResponse, include_in_schema=False)
def request_drive_check(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _enqueue_response(
        request,
        csrf_token,
        lambda session: enqueue_drive_check(session, user_id=_principal(request).user_id),
    )


@router.post("/upload/{backup_record_id}", response_class=HTMLResponse, include_in_schema=False)
def request_drive_upload(
    request: Request,
    backup_record_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _enqueue_response(
        request,
        csrf_token,
        lambda session: enqueue_drive_upload(
            session,
            backup_record_id=backup_record_id,
            user_id=_principal(request).user_id,
            trigger="MANUAL",
        ),
    )


@router.post("/download/{backup_record_id}", response_class=HTMLResponse, include_in_schema=False)
def request_drive_download(
    request: Request,
    backup_record_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _enqueue_response(
        request,
        csrf_token,
        lambda session: enqueue_drive_download(
            session,
            backup_record_id=backup_record_id,
            user_id=_principal(request).user_id,
        ),
    )


@router.post("/delete/{backup_record_id}", response_class=HTMLResponse, include_in_schema=False)
def request_drive_delete(
    request: Request,
    backup_record_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    return _enqueue_response(
        request,
        csrf_token,
        lambda session: enqueue_drive_delete(
            session,
            backup_record_id=backup_record_id,
            user_id=_principal(request).user_id,
        ),
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def drive_job_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None or job.kind not in DRIVE_JOB_KINDS:
            raise HTTPException(status_code=404)
        return _job_response(request, job)


@router.post("/jobs/{job_id}/cancel", response_class=HTMLResponse, include_in_schema=False)
def cancel_drive_job(
    request: Request,
    job_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    with session_scope(_session_factory(request)) as session:
        if not request_drive_cancel(session, job_id, user_id=_principal(request).user_id):
            return PlainTextResponse("O job não pode mais ser cancelado.", status_code=409)
        return _job_response(request, session.get_one(Job, job_id))


def _enqueue_response(
    request: Request,
    csrf_token: str | None,
    enqueue: Callable[[Session], Job],
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue(session)
            return _job_response(request, job, status_code=202)
    except DriveJobRequestError as error:
        return _job_response(request, None, error=str(error), status_code=400)
    except DriveJobConflictError as error:
        with session_scope(_session_factory(request)) as session:
            return _job_response(
                request,
                latest_drive_job(session),
                error=str(error),
                status_code=409,
            )


def _remote_list_items(session: Session) -> tuple[RemoteBackupListItem, ...]:
    timezone = _configured_timezone(session)
    records = tuple(
        session.scalars(
            select(BackupRecord)
            .where(BackupRecord.location == "DRIVE", BackupRecord.status == "VALID")
            .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        )
    )
    local_names = set(
        session.scalars(
            select(BackupRecord.filename).where(
                BackupRecord.location == "LOCAL", BackupRecord.status == "VALID"
            )
        )
    )
    jobs = {
        job.id: job
        for job in session.scalars(
            select(Job).where(Job.id.in_([record.job_id for record in records if record.job_id]))
        )
    }
    return tuple(
        RemoteBackupListItem(
            id=record.id,
            filename=record.filename,
            status=record.status,
            sha256=record.sha256 or "indisponível",
            size=_format_bytes(record.size_bytes or 0),
            created_at=_localized(record.created_at, timezone),
            job_id=record.job_id,
            job_status=(
                jobs[record.job_id].status
                if record.job_id is not None and record.job_id in jobs
                else "SEM JOB"
            ),
            available_locally=record.filename in local_names,
        )
        for record in records
    )


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


def _localized(value: object, timezone: ZoneInfo) -> str:
    from datetime import datetime

    if not isinstance(value, datetime):
        return "indisponível"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).strftime("%d/%m/%Y %H:%M:%S %Z")


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")
