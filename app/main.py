from fastapi import FastAPI

from app.config import Settings
from app.health.router import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(
        title="Palworld Manager",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = resolved_settings
    application.include_router(health_router)
    return application


app = create_app()
