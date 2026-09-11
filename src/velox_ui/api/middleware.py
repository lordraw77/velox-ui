"""Request-scoped middleware: correlation ids and HTTP metrics.

Both are written as pure ASGI middleware rather than ``BaseHTTPMiddleware``. That is
not a stylistic preference: ``BaseHTTPMiddleware`` wraps the response in an anyio task
group and an internal queue, which adds latency to every chunk of a streaming response
— exactly the thing this project exists to avoid.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from velox_ui.ids import new_ulid
from velox_ui.metrics import METRICS

__all__ = ["MetricsMiddleware", "RequestContextMiddleware"]

REQUEST_ID_HEADER = b"x-request-id"


class RequestContextMiddleware:
    """Attach a correlation id to every request and echo it back.

    An inbound ``X-Request-ID`` is honoured so a reverse proxy's id flows through into
    our logs; otherwise one is generated.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inject the request id into the scope and the response headers."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = _header(scope, REQUEST_ID_HEADER)
        request_id = inbound.decode("ascii", "replace")[:64] if inbound else new_ulid()
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_id)


class MetricsMiddleware:
    """Count requests and time the non-streaming ones.

    Duration is recorded at ``http.response.start``, which is time-to-first-byte rather
    than time-to-last-byte. For a streaming completion the second number is the
    generation length of the model and says nothing about this server; the first is the
    part we are accountable for.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Record a request outcome."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        method = scope.get("method", "GET")
        status_holder: dict[str, Any] = {"code": 500}

        async def send_recording(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                route = _route_template(scope)
                METRICS.http_duration.labels(method=method, route=route).observe(
                    time.perf_counter() - started
                )
            await send(message)

        try:
            await self.app(scope, receive, send_recording)
        finally:
            METRICS.http_requests.labels(
                method=method,
                route=_route_template(scope),
                status=str(status_holder["code"]),
            ).inc()


def _route_template(scope: Scope) -> str:
    """Return the matched route pattern, never the raw path.

    Labelling with the raw path would let any client create unbounded metric series
    simply by requesting random URLs.
    """
    route = scope.get("route")
    path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
    return str(path_format) if path_format else "unmatched"


def _header(scope: Scope, name: bytes) -> bytes | None:
    """Return one raw request header, or ``None``."""
    for key, value in scope.get("headers", ()):
        if key == name:
            return bytes(value)
    return None
