from app.health.router import health
from app.main import app


def test_health_returns_minimal_status() -> None:
    assert health() == {"status": "ok"}


def test_health_route_is_registered() -> None:
    assert str(app.url_path_for("health")) == "/health"
