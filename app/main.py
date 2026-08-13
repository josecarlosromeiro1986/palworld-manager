from fastapi import FastAPI

from app.health.router import router as health_router

app = FastAPI(
    title="Palworld Manager",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(health_router)
