import uvicorn

from app.config import AppEnvironment, Settings


def run() -> None:
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=str(settings.app_host),
        port=settings.app_port,
        reload=settings.environment is AppEnvironment.DEVELOPMENT,
    )


if __name__ == "__main__":
    run()
