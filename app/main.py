from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.middleware import AuthenticationMiddleware
from app.auth.router import router as auth_router
from app.config import AppEnvironment, Settings
from app.dashboard.metrics import HostMetricsService
from app.dashboard.router import router as dashboard_router
from app.db.engine import create_database_engine, create_session_factory
from app.health.palworld import create_palworld_health_check
from app.health.router import router as health_router
from app.lifecycle.fake import PersistentFakePalworldEnvironment
from app.logs.router import router as logs_router
from app.logs.service import create_palworld_log_source
from app.system.palworld_service import create_palworld_service


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
    application.state.metrics_service = HostMetricsService()
    application.state.palworld_log_source = create_palworld_log_source(resolved_settings)
    if resolved_settings.environment is AppEnvironment.PRODUCTION:
        palworld_service = create_palworld_service(resolved_settings)
        palworld_health_check = create_palworld_health_check(
            resolved_settings,
            palworld_service,
        )
    else:
        fake_environment = PersistentFakePalworldEnvironment(session_factory)
        palworld_service = fake_environment
        palworld_health_check = fake_environment
    application.state.palworld_service = palworld_service
    application.state.palworld_health_check = palworld_health_check
    application.add_middleware(AuthenticationMiddleware, session_factory=session_factory)
    application.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(dashboard_router)
    application.include_router(logs_router)
    return application


app = create_app()
