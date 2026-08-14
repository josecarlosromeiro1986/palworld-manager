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
    return templates.TemplateResponse(
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


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def backups_page(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        timezone = _configured_timezone(session)
        records = tuple(
            session.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc()))
        )
        jobs = {
            job.id: job
            for job in session.scalars(
                select(Job).where(
                    Job.id.in_([record.job_id for record in records if record.job_id])
                )
            )
        }
        items = tuple(
            _list_item(
                record,
                jobs.get(record.job_id) if record.job_id is not None else None,
                timezone,
            )
            for record in records
        )
        latest = latest_backup_job(session)
        latest_view = backup_job_view(latest) if latest is not None else None
        latest_lines = (
            _job_logs(request).tail(latest.log_path)
            if latest is not None and latest.log_path
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
        },
    )


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


def _list_item(record: BackupRecord, job: Job | None, timezone: ZoneInfo) -> BackupListItem:
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
    )


def _format_bytes(value: int) -> str:
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")
