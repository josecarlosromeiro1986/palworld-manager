from fastapi import Request, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp

from app.auth.cookies import SESSION_COOKIE_NAME
from app.auth.sessions import SessionPrincipal, resolve_session
from app.db.engine import session_scope

PUBLIC_PATHS = frozenset({"/health", "/login"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, session_factory: sessionmaker[Session]) -> None:
        super().__init__(app)
        self._session_factory = session_factory

    def _resolve(self, token: str | None) -> SessionPrincipal | None:
        with session_scope(self._session_factory) as session:
            return resolve_session(session, token)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        principal = await run_in_threadpool(
            self._resolve,
            request.cookies.get(SESSION_COOKIE_NAME),
        )
        if principal is None:
            if request.method in SAFE_METHODS:
                return RedirectResponse("/login", status_code=303)
            return PlainTextResponse("Autenticação necessária.", status_code=401)

        request.state.principal = principal
        return await call_next(request)
