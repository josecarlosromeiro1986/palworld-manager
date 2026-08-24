from pathlib import Path
from typing import Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit.history import (
    AuditFilterError,
    AuditFilters,
    AuditHistoryPage,
    parse_audit_filters,
)
from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.sessions import SessionPrincipal

router = APIRouter(prefix="/audit")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


class AuditHistoryReader(Protocol):
    def search(self, filters: AuditFilters) -> AuditHistoryPage: ...


def _history(request: Request) -> AuditHistoryReader:
    return cast(AuditHistoryReader, request.app.state.audit_history_service)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def audit_history(request: Request) -> Response:
    query = request.query_params
    filter_error: str | None = None
    status_code = 200
    try:
        filters = parse_audit_filters(
            date_from=query.get("date_from"),
            date_to=query.get("date_to"),
            action=query.get("action"),
            result=query.get("result"),
            origin=query.get("origin"),
            user_id=query.get("user_id"),
            target=query.get("target"),
            page=query.get("page"),
        )
    except AuditFilterError as error:
        filters = AuditFilters()
        filter_error = str(error)
        status_code = 400
    history = _history(request).search(filters)
    return templates.TemplateResponse(
        request=request,
        name="audit/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "audit",
            "history": history,
            "filter_error": filter_error,
        },
        status_code=status_code,
    )
