from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.middleware import AuthenticationMiddleware
from app.auth.router import router as auth_router
from app.backups.drive_router import router as drive_backups_router
from app.backups.router import router as backups_router
from app.config import AppEnvironment, Settings
from app.dashboard.metrics import HostMetricsService
from app.dashboard.router import router as dashboard_router
from app.db.engine import create_database_engine, create_session_factory
from app.health.palworld import create_palworld_health_check
from app.health.router import router as health_router
from app.integrations.palworld_rest import create_palworld_rest_client
from app.jobs.health import (
    FakeWorkerService,
    SystemdWorkerService,
    WorkerHealthChecker,
    WorkerService,
)
from app.jobs.logs import create_job_log_store
from app.lifecycle.fake import PersistentFakePalworldEnvironment
from app.logs.router import router as logs_router
from app.logs.service import create_palworld_log_source
from app.palworld_settings.router import router as palworld_settings_router
from app.palworld_settings.service import PalworldSettingsService
from app.palworld_settings.storage import create_palworld_settings_storage
from app.players.administration import PlayerAdministrationService
from app.players.router import router as players_router
from app.players.service import ManualPlayersService
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
    application.state.job_log_store = create_job_log_store(resolved_settings.manager_database)
    palworld_rest_client = create_palworld_rest_client(resolved_settings)
    application.state.palworld_rest_client = palworld_rest_client
    application.state.players_service = ManualPlayersService(palworld_rest_client)
    application.state.player_administration_service = PlayerAdministrationService(
        palworld_rest_client,
        session_factory,
    )
    palworld_settings_storage = create_palworld_settings_storage(resolved_settings)
    application.state.palworld_settings_storage = palworld_settings_storage
    application.state.palworld_settings_service = PalworldSettingsService(
        palworld_settings_storage,
        session_factory,
    )
    worker_service: WorkerService
    if resolved_settings.environment is AppEnvironment.PRODUCTION:
        palworld_service = create_palworld_service(resolved_settings)
        palworld_health_check = create_palworld_health_check(
            resolved_settings,
            palworld_service,
        )
        worker_service = SystemdWorkerService()
    else:
        fake_environment = PersistentFakePalworldEnvironment(session_factory)
        palworld_service = fake_environment
        palworld_health_check = fake_environment
        worker_service = FakeWorkerService()
    application.state.palworld_service = palworld_service
    application.state.palworld_health_check = palworld_health_check
    application.state.worker_health_check = WorkerHealthChecker(session_factory, worker_service)
    application.add_middleware(AuthenticationMiddleware, session_factory=session_factory)
    application.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(backups_router)
    application.include_router(drive_backups_router)
    application.include_router(dashboard_router)
    application.include_router(players_router)
    application.include_router(logs_router)
    application.include_router(palworld_settings_router)
    return application


app = create_app()
