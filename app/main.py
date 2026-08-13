from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.middleware import AuthenticationMiddleware
from app.auth.router import router as auth_router
from app.config import Settings
from app.db.engine import create_database_engine, create_session_factory
from app.health.router import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    engine = create_database_engine(resolved_settings.manager_database)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    application = FastAPI(
        title="Palworld Manager",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.session_factory = session_factory
    application.add_middleware(AuthenticationMiddleware, session_factory=session_factory)
    application.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    application.include_router(health_router)
    application.include_router(auth_router)
    return application


app = create_app()
