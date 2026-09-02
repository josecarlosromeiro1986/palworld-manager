import ast
import re
import tomllib
from pathlib import Path

from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from app import __version__
from app.security import RequestBodyLimitMiddleware, SecurityHeadersMiddleware
from app.system.commands import (
    rclone_subprocess_environment,
    sanitized_subprocess_environment,
)

PROJECT_ROOT = Path(__file__).parents[2]


def test_release_version_is_consistent() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == "1.0.0"
    assert project["project"]["version"] == __version__


def test_templates_do_not_require_inline_javascript() -> None:
    inline_script = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)

    for template in (PROJECT_ROOT / "app/templates").rglob("*.html"):
        assert inline_script.search(template.read_text(encoding="utf-8")) is None, template


def test_subprocess_environment_omits_application_secrets() -> None:
    source = {
        "HOME": "/var/lib/palworld-manager",
        "PATH": "/usr/bin:/bin",
        "PALWORLD_REST_USERNAME": "usuario-protegido",
        "PALWORLD_REST_PASSWORD": "senha-protegida",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/segredo",
        "UNRELATED_SECRET": "valor-protegido",
    }

    environment = sanitized_subprocess_environment(source)
    rclone_environment = rclone_subprocess_environment(
        Path("/var/lib/palworld-manager/rclone/rclone.conf"),
        source,
    )

    assert environment == {
        "HOME": "/var/lib/palworld-manager",
        "PATH": "/usr/bin:/bin",
    }
    assert rclone_environment == {
        **environment,
        "RCLONE_CONFIG": "/var/lib/palworld-manager/rclone/rclone.conf",
    }
    assert not any("senha" in value or "segredo" in value for value in rclone_environment.values())


def test_all_application_subprocesses_disable_shell_and_receive_sanitized_environment() -> None:
    calls: list[tuple[Path, ast.Call]] = []
    for source in (PROJECT_ROOT / "app").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in {"run", "Popen"}
            ):
                calls.append((source, node))

    assert calls
    for source, call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "env" in keywords, source
        shell = keywords.get("shell")
        assert isinstance(shell, ast.Constant), source
        assert shell.value is False, source


def test_security_headers_cover_public_responses_and_hsts_is_production_only() -> None:
    async def endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    production = Starlette(routes=[Route("/", endpoint)])
    production.add_middleware(SecurityHeadersMiddleware, production=True)
    development = Starlette(routes=[Route("/", endpoint)])
    development.add_middleware(SecurityHeadersMiddleware, production=False)

    production_response = TestClient(production).get("/")
    development_response = TestClient(development).get("/")

    assert production_response.headers["cache-control"] == "no-store"
    assert production_response.headers["content-security-policy"].startswith("default-src 'self'")
    assert production_response.headers["x-content-type-options"] == "nosniff"
    assert production_response.headers["x-frame-options"] == "DENY"
    assert production_response.headers["referrer-policy"] == "no-referrer"
    assert production_response.headers["strict-transport-security"] == "max-age=31536000"
    assert "strict-transport-security" not in development_response.headers


def test_request_body_limit_rejects_declared_and_streamed_oversize() -> None:
    async def endpoint(request: Request) -> PlainTextResponse:
        return PlainTextResponse((await request.body()).decode("ascii"))

    application = Starlette(routes=[Route("/", endpoint, methods=["POST"])])
    application.add_middleware(RequestBodyLimitMiddleware, maximum_bytes=4)
    client = TestClient(application)

    accepted = client.post("/", content=b"1234")
    rejected = client.post("/", content=b"12345")
    streamed = client.post("/", content=(chunk for chunk in (b"12", b"345")))
    duplicated_length = client.post(
        "/",
        content=b"1",
        headers=[("content-length", "1"), ("content-length", "1")],
    )

    assert accepted.status_code == 200
    assert accepted.text == "1234"
    assert rejected.status_code == 413
    assert streamed.status_code == 413
    assert duplicated_length.status_code == 400
