from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.csrf import tokens_match
from app.auth.sessions import SessionPrincipal, session_csrf_is_valid
from app.db.engine import session_scope
from app.db.models import Job
from app.jobs.logs import JobLogStore
from app.jobs.service import TERMINAL_JOB_STATUSES
from app.updates.jobs import (
    UPDATE_CHECK_JOB_KIND,
    UPDATE_JOB_KIND,
    UpdateJobConflictError,
    UpdateRequestError,
    enqueue_update,
    enqueue_update_check,
    latest_update_check,
    latest_update_job,
    request_update_cancel,
    update_job_view,
)

router = APIRouter(prefix="/updates")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


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
    mode: str,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    lines: tuple[str, ...] = ()
    if job is not None and job.log_path:
        try:
            lines = _job_logs(request).tail(job.log_path)
        except (OSError, ValueError):
            lines = ()
    response = templates.TemplateResponse(
        request=request,
        name="updates/_job.html",
        context={
            "job": update_job_view(job) if job is not None else None,
            "mode": mode,
            "job_log_lines": lines,
            "error": error,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
        },
        status_code=status_code,
    )
    if job is not None and job.status in TERMINAL_JOB_STATUSES:
        response.headers["HX-Trigger"] = "palworldUpdateJobFinished"
    return response


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def updates_page(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        check = latest_update_check(session)
        update_job = latest_update_job(session)
        check_view = update_job_view(check) if check is not None else None
        update_view = update_job_view(update_job) if update_job is not None else None
        check_lines = _safe_log_tail(request, check)
        update_lines = _safe_log_tail(request, update_job)
    return templates.TemplateResponse(
        request=request,
        name="updates/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "updates",
            "check_job": check_view,
            "check_job_log_lines": check_lines,
            "update_job": update_view,
            "update_job_log_lines": update_lines,
        },
    )


@router.post("/check", response_class=HTMLResponse, include_in_schema=False)
def request_update_check(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_update_check(session, user_id=_principal(request).user_id)
            return _job_response(request, job, mode="check", status_code=202)
    except UpdateJobConflictError as error:
        with session_scope(_session_factory(request)) as session:
            return _job_response(
                request,
                latest_update_check(session),
                mode="check",
                error=str(error),
                status_code=409,
            )


@router.post("", response_class=HTMLResponse, include_in_schema=False)
def request_update(
    request: Request,
    confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_update(
                session,
                confirmation=confirmation,
                user_id=_principal(request).user_id,
            )
            return _job_response(request, job, mode="update", status_code=202)
    except UpdateRequestError as error:
        return _job_response(
            request,
            None,
            mode="update",
            error=str(error),
            status_code=400,
        )
    except UpdateJobConflictError as error:
        with session_scope(_session_factory(request)) as session:
            return _job_response(
                request,
                latest_update_job(session),
                mode="update",
                error=str(error),
                status_code=409,
            )


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def update_job_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None or job.kind not in {UPDATE_CHECK_JOB_KIND, UPDATE_JOB_KIND}:
            raise HTTPException(status_code=404)
        mode = "check" if job.kind == UPDATE_CHECK_JOB_KIND else "update"
        return _job_response(request, job, mode=mode)


@router.post("/jobs/{job_id}/cancel", response_class=HTMLResponse, include_in_schema=False)
def cancel_update(
    request: Request,
    job_id: int,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    with session_scope(_session_factory(request)) as session:
        if not request_update_cancel(session, job_id, user_id=_principal(request).user_id):
            return PlainTextResponse("O Update não pode mais ser cancelado.", status_code=409)
        return _job_response(request, session.get_one(Job, job_id), mode="update")


def _safe_log_tail(request: Request, job: Job | None) -> tuple[str, ...]:
    if job is None or not job.log_path:
        return ()
    try:
        return _job_logs(request).tail(job.log_path)
    except (OSError, ValueError):
        return ()
