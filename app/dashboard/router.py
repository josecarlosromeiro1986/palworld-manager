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
from app.dashboard.metrics import HostMetricsService, MetricsSnapshot
from app.db.engine import session_scope
from app.db.models import Job
from app.health.palworld import PalworldHealthChecker, PalworldHealthState
from app.integrations.palworld_rest import RestApiState
from app.lifecycle.jobs import (
    LifecycleJobConflictError,
    enqueue_lifecycle_job,
    lifecycle_job_view,
)
from app.lifecycle.service import LifecycleAction

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _metrics_service(request: Request) -> HostMetricsService:
    return cast(HostMetricsService, request.app.state.metrics_service)


def _palworld_health_check(request: Request) -> PalworldHealthChecker:
    return cast(PalworldHealthChecker, request.app.state.palworld_health_check)


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


def _format_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            precision = 0 if unit == "B" else 1
            return f"{amount:.{precision}f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _chart_data(snapshot: MetricsSnapshot) -> dict[str, list[float] | list[str]]:
    return {
        "labels": [point.measured_at.isoformat() for point in snapshot.history],
        "cpu": [point.cpu_percent for point in snapshot.history],
        "memory": [point.memory_percent for point in snapshot.history],
        "network_received": [point.network_received_bytes_per_second for point in snapshot.history],
        "network_sent": [point.network_sent_bytes_per_second for point in snapshot.history],
    }


@router.get("/metrics", response_class=HTMLResponse, include_in_schema=False)
def metrics_fragment(request: Request) -> Response:
    snapshot = _metrics_service(request).collect()
    current = snapshot.current
    return templates.TemplateResponse(
        request=request,
        name="dashboard/_metrics.html",
        context={
            "metrics": current,
            "memory_used": _format_bytes(current.memory_used_bytes),
            "memory_total": _format_bytes(current.memory_total_bytes),
            "disk_free": _format_bytes(current.disk_free_bytes),
            "disk_total": _format_bytes(current.disk_total_bytes),
            "network_received": _format_bytes(current.network_received_bytes_per_second),
            "network_sent": _format_bytes(current.network_sent_bytes_per_second),
            "chart_data": _chart_data(snapshot),
        },
    )


@router.get("/palworld-health", response_class=HTMLResponse, include_in_schema=False)
def palworld_health_fragment(request: Request) -> Response:
    health = _palworld_health_check(request).check()
    descriptions = {
        PalworldHealthState.ONLINE: "systemd, processo e REST API saudáveis",
        PalworldHealthState.STARTING: "o serviço ainda está iniciando",
        PalworldHealthState.DEGRADED: "o servidor responde apenas parcialmente",
        PalworldHealthState.OFFLINE: "o servidor está parado",
        PalworldHealthState.FAILURE: "falha ou estado inconsistente detectado",
    }
    rest_labels = {
        RestApiState.AVAILABLE: "DISPONÍVEL",
        RestApiState.UNAUTHORIZED: "NÃO AUTORIZADA",
        RestApiState.UNAVAILABLE: "INDISPONÍVEL",
        RestApiState.INVALID_RESPONSE: "RESPOSTA INVÁLIDA",
        RestApiState.FAILURE: "FALHA",
    }
    return templates.TemplateResponse(
        request=request,
        name="dashboard/_palworld_health.html",
        context={
            "health": health,
            "description": descriptions[health.state],
            "service_label": health.service_state.upper()
            if health.service_state is not None
            else "INDISPONÍVEL",
            "process_label": (
                "ATIVO"
                if health.process_running is True
                else "INATIVO"
                if health.process_running is False
                else "INDISPONÍVEL"
            ),
            "rest_label": rest_labels[health.rest_api_state],
        },
    )


@router.post("/lifecycle/{action}", response_class=HTMLResponse, include_in_schema=False)
def request_lifecycle_action(
    request: Request,
    action: LifecycleAction,
    confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    if not _valid_session_csrf(request, csrf_token):
        return PlainTextResponse("Token CSRF inválido.", status_code=403)
    if confirmation != action.value:
        return PlainTextResponse("Confirmação inválida.", status_code=400)

    principal = _principal(request)
    try:
        with session_scope(_session_factory(request)) as session:
            job = enqueue_lifecycle_job(session, action, user_id=principal.user_id)
            view = lifecycle_job_view(job)
    except LifecycleJobConflictError:
        return templates.TemplateResponse(
            request=request,
            name="dashboard/_lifecycle_job.html",
            context={"job": None, "error": "Já existe uma ação do servidor em andamento."},
        )
    return templates.TemplateResponse(
        request=request,
        name="dashboard/_lifecycle_job.html",
        context={"job": view, "error": None},
        status_code=202,
    )


@router.get("/lifecycle/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def lifecycle_job_status(request: Request, job_id: int) -> Response:
    with session_scope(_session_factory(request)) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404)
        try:
            view = lifecycle_job_view(job)
        except ValueError as error:
            raise HTTPException(status_code=404) from error
    return templates.TemplateResponse(
        request=request,
        name="dashboard/_lifecycle_job.html",
        context={"job": view, "error": None},
    )
