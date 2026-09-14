"""Application factory.

Everything the HTTP layer needs is assembled here: middleware, exception handlers and
routers. Two choices are worth stating plainly.

First, error handling is centralised. Every failure that reaches a client is rendered
through :class:`~velox_ui.errors.VeloxError`, so the response carries a code the UI can
act on and a message a person can read, and an unexpected exception is logged with its
correlation id but never leaked to the caller.

Second, middleware order matters and is not incidental: the correlation id is
outermost so that it exists for every log line, including those written while handling
an error, and metrics sit inside it so they observe the real handler duration.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from velox_ui import __version__
from velox_ui.api.middleware import MetricsMiddleware, RequestContextMiddleware
from velox_ui.api.routes import (
    admin,
    apikeys,
    auth,
    chats,
    custom_models,
    folders,
    local_models,
    mcp,
    models,
    providers,
    rag,
    search,
    system,
    tags,
    tools,
)
from velox_ui.api.security_headers import SecurityHeadersMiddleware
from velox_ui.api.static_files import mount_frontend
from velox_ui.errors import ErrorCode, VeloxError
from velox_ui.lifespan import lifespan_for
from velox_ui.settings import Settings, load_settings

__all__ = ["create_app"]

_log = logging.getLogger("velox.app")

_DESCRIPTION = """
A fast, self-hosted frontend for local and remote language models.

Local backends are the primary case: no API key is required, model loading is reported
as a distinct state rather than a timeout, and an unreachable host degrades to an
offline badge instead of an error.
""".strip()


def _request_id(request: Request) -> str | None:
    """Return the correlation id attached by the context middleware."""
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Args:
        settings: Configuration to use. Loaded from the environment and the
            configuration file when omitted.

    Returns:
        A configured :class:`fastapi.FastAPI` instance.
    """
    config = settings if settings is not None else load_settings()

    app = FastAPI(
        title="velox-ui",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan_for(config),  # type: ignore[arg-type]
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    _install_error_handlers(app)

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(apikeys.router)
    app.include_router(chats.router)
    app.include_router(models.router)
    app.include_router(providers.router)
    app.include_router(local_models.router)
    app.include_router(local_models.jobs_router)
    app.include_router(folders.router)
    app.include_router(tags.router)
    app.include_router(search.router)
    app.include_router(custom_models.router)
    app.include_router(rag.router)
    app.include_router(mcp.router)
    app.include_router(tools.router)
    app.include_router(admin.router)

    # Last, so the single-page fallback cannot shadow an API route.
    mount_frontend(app)

    return app


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    """Reduce pydantic's error list to a JSON-safe, stable shape.

    Pydantic's raw errors carry an ``input`` value and a ``ctx`` that can hold
    arbitrary objects, including the submitted password. Only the field path, the
    message and the error type are echoed back.
    """
    details: list[dict[str, str]] = []
    for error in exc.errors():
        location = error.get("loc", ())
        details.append(
            {
                "field": ".".join(str(part) for part in location[1:]),
                "message": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return details


def _install_error_handlers(app: FastAPI) -> None:
    """Register the handlers that render every error in one shape."""

    @app.exception_handler(VeloxError)
    async def _velox_error(request: Request, exc: VeloxError) -> JSONResponse:
        headers: dict[str, str] = {}
        if exc.retry_after_s is not None:
            headers["Retry-After"] = str(int(exc.retry_after_s))
        if exc.status_code >= 500:
            _log.error(
                "request failed: %s", exc.message, extra={"code": str(exc.code)}, exc_info=exc
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(request_id=_request_id(request)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body is a list of pydantic error dicts, which is a
        # different shape from every other error this API returns. Normalising it
        # means the client has exactly one error parser.
        details = _validation_details(exc)
        fields = sorted({item["field"] for item in details if item["field"]})
        message = "The request is not valid."
        if fields:
            message = f"The request is not valid. Check: {', '.join(fields)}."
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": str(ErrorCode.INVALID_REQUEST),
                    "message": message,
                    "retryable": False,
                    "fields": details,
                    "request_id": _request_id(request),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.INVALID_REQUEST,
            409: ErrorCode.CONFLICT,
            429: ErrorCode.RATE_LIMITED,
        }.get(exc.status_code, ErrorCode.INTERNAL)
        detail: Any = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": str(code),
                    "message": detail,
                    "retryable": exc.status_code in (429, 503),
                    "request_id": _request_id(request),
                }
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The traceback goes to the log with the correlation id; the client gets the
        # id and nothing else. Leaking internals here is how stack traces end up in
        # screenshots and bug reports.
        request_id = _request_id(request)
        _log.exception("unhandled error", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": str(ErrorCode.INTERNAL),
                    "message": "An unexpected error occurred. "
                    "The details are in the server log.",
                    "retryable": False,
                    "request_id": request_id,
                }
            },
        )
