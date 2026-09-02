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
from app.host_power.jobs import (
    HOST_POWER_JOB_KINDS,
    HostPowerJobConflictError,
    HostPowerRequestError,
    enqueue_host_power_job,
    host_power_job_view,
    latest_host_power_job,
)
from app.jobs.logs import JobLogStore
from app.system.host_power import HostPowerAction

router = APIRouter(prefix="/host-power")
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
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    lines: tuple[str, ...] = ()
    if job is not None and job.log_path:
        try:
            lines = _job_logs(request).tail(job.log_path)
        except (OSError, ValueError):
            lines = ()
    return templates.TemplateResponse(
        request=request,
        name="host_power/_job.html",
        context={
            "job": host_power_job_view(job) if job is not None else None,
            "job_log_lines": lines,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
def host_power_status(request: Request) -> Response:
    with session_scope(_session_factory(request)) as session:
        return _job_response(request, latest_host_power_job(session))


@router.post("/{action}", response_class=HTMLResponse, include_in_schema=False)
def request_host_power(
    request: Request,
    action: HostPowerAction,
    confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_host_power_job(
                session,
                action,
                confirmation=confirmation,
                user_id=_principal(request).user_id,
            )
            return _job_response(request, job, status_code=202)
    except HostPowerRequestError as error:
        return _job_response(request, None, error=str(error), status_code=400)
    except HostPowerJobConflictError as error:
        with session_scope(_session_factory(request)) as session:
            return _job_response(
                request,
                latest_host_power_job(session),
                error=str(error),
                status_code=409,
            )


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def host_power_job_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None or job.kind not in HOST_POWER_JOB_KINDS:
            raise HTTPException(status_code=404)
        return _job_response(request, job)
