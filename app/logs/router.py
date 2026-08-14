import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.auth.cookies import SESSION_CSRF_COOKIE_NAME
from app.auth.sessions import SessionPrincipal
from app.logs.service import (
    LogEntry,
    PalworldLogError,
    PalworldLogSource,
    validate_cursor,
    validate_history_limit,
)

router = APIRouter(prefix="/logs")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _log_source(request: Request) -> PalworldLogSource:
    return cast(PalworldLogSource, request.app.state.palworld_log_source)


def _principal(request: Request) -> SessionPrincipal:
    return cast(SessionPrincipal, request.state.principal)


def _event_payload(entry: LogEntry) -> dict[str, str]:
    return {
        "cursor": entry.cursor,
        "occurred_at": entry.occurred_at.isoformat(),
        "message": entry.message,
        "category": entry.category.value,
    }


def encode_sse(entry: LogEntry) -> str:
    payload = json.dumps(_event_payload(entry), ensure_ascii=False, separators=(",", ":"))
    return f"id: {entry.cursor}\nretry: 2000\nevent: log\ndata: {payload}\n\n"


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def log_page(
    request: Request,
    lines: Annotated[int, Query()] = 100,
) -> Response:
    try:
        line_limit = validate_history_limit(lines)
    except ValueError as validation_error:
        raise HTTPException(
            status_code=422, detail="Quantidade de linhas inválida."
        ) from validation_error
    page_error: str | None = None
    try:
        entries = _log_source(request).history(line_limit)
    except PalworldLogError:
        entries = []
        page_error = "Não foi possível consultar os logs do servidor. Tente novamente."
    return templates.TemplateResponse(
        request=request,
        name="logs/index.html",
        context={
            "username": _principal(request).username,
            "csrf_token": request.cookies.get(SESSION_CSRF_COOKIE_NAME),
            "active_navigation": "logs",
            "entries": entries,
            "category_labels": {
                "ERROR": "ERRO",
                "WARNING": "AVISO",
                "CONNECTION": "CONEXÃO",
                "SYSTEM": "SISTEMA",
                "NORMAL": "NORMAL",
            },
            "line_limit": line_limit,
            "last_cursor": entries[-1].cursor if entries else "",
            "error": page_error,
        },
    )


@router.get("/stream", include_in_schema=False)
def stream_logs(
    request: Request,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Response:
    try:
        after_cursor = validate_cursor(last_event_id or cursor)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Cursor de logs inválido.") from error

    def events() -> Iterator[str]:
        yield "retry: 2000\n\n"
        try:
            for entry in _log_source(request).stream(after_cursor):
                if entry is None:
                    yield ": keepalive\n\n"
                else:
                    yield encode_sse(entry)
        except PalworldLogError:
            yield 'event: stream-error\ndata: {"message":"stream_indisponivel"}\n\n'

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
