from pathlib import Path
from typing import Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.sessions import SessionPrincipal
from app.diagnostics.models import DiagnosticReport

router = APIRouter(prefix="/diagnostics")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


class DiagnosticsRunner(Protocol):
    def run(self) -> DiagnosticReport: ...


def _diagnostics(request: Request) -> DiagnosticsRunner:
    return cast(DiagnosticsRunner, request.app.state.diagnostics_service)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _report_response(request: Request) -> Response:
    report = _diagnostics(request).run()
    return templates.TemplateResponse(
        request=request,
        name="diagnostics/_report.html",
        context={"report": report},
    )


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def diagnostics_page(request: Request) -> Response:
    report = _diagnostics(request).run()
    return templates.TemplateResponse(
        request=request,
        name="diagnostics/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "diagnostics",
            "report": report,
        },
    )


@router.get("/report", response_class=HTMLResponse, include_in_schema=False)
def diagnostics_report(request: Request) -> Response:
    return _report_response(request)
