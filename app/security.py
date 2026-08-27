from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES: Final = 1024 * 1024
SAFE_HTTP_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'"
)


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, maximum_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        if maximum_bytes <= 0:
            raise ValueError("o limite do corpo HTTP deve ser positivo")
        self._app = app
        self._maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in SAFE_HTTP_METHODS:
            await self._app(scope, receive, send)
            return

        content_lengths = [
            value for name, value in scope.get("headers", ()) if name.lower() == b"content-length"
        ]
        if len(content_lengths) > 1:
            await _plain_response(send, 400, "Content-Length inválido.")
            return
        if content_lengths:
            raw_content_length = content_lengths[0]
            if not raw_content_length.isdigit():
                await _plain_response(send, 400, "Content-Length inválido.")
                return
            content_length = int(raw_content_length)
            if content_length > self._maximum_bytes:
                await _plain_response(send, 413, "Corpo da requisição excede o limite permitido.")
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self._maximum_bytes:
                await _plain_response(send, 413, "Corpo da requisição excede o limite permitido.")
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self._app(scope, replay, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self._app = app
        self._production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", ()))
                existing = {name.lower() for name, _value in headers}
                for name, value in self._headers(scope).items():
                    encoded_name = name.encode("ascii")
                    if encoded_name not in existing:
                        headers.append((encoded_name, value.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, add_headers)

    def _headers(self, scope: Scope) -> dict[str, str]:
        headers = {
            "content-security-policy": CONTENT_SECURITY_POLICY,
            "cross-origin-resource-policy": "same-origin",
            "permissions-policy": "camera=(), geolocation=(), microphone=()",
            "referrer-policy": "no-referrer",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
        }
        path = str(scope.get("path", ""))
        if not path.startswith("/static/"):
            headers["cache-control"] = "no-store"
        if self._production:
            headers["strict-transport-security"] = "max-age=31536000"
        return headers


async def _plain_response(send: Send, status_code: int, text: str) -> None:
    body = text.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
