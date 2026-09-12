"""Serving the built interface.

The frontend is compiled into ``velox_ui/web`` at build time, so a wheel carries the
interface with it: ``pip install velox-ui`` needs no Node, and the container has no
second service in front of it. That is one of the project's anti-requirements — no
mandatory Node at runtime.

Three details are the whole reason this is a module rather than one ``StaticFiles``
mount:

* **Hashed assets are immutable, the entry document is not.** Vite fingerprints every
  asset, so those can be cached for a year; ``index.html`` names them and must never be
  cached, or a browser keeps loading last week's app.
* **Unknown paths fall back to the document, but API paths must not.** A single-page
  app needs deep links to return the shell, while a mistyped API path has to stay a
  JSON 404 — returning HTML there would give a client an unparseable body.
* **A missing build is a clear message, not a stack trace.** Running from a source
  checkout without having built the frontend is a normal state during development.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from velox_ui.errors import ErrorCode

__all__ = ["WEB_ROOT", "mount_frontend"]

_log = logging.getLogger("velox.static")

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

# Paths owned by the server. Anything under these is never answered with the SPA shell.
_API_PREFIXES = ("/api", "/v1", "/health", "/ready", "/metrics")

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache, must-revalidate"


class _HashedAssets(StaticFiles):
    """Static files with a cache policy that matches how Vite names them."""

    def file_response(self, *args: object, **kwargs: object) -> Response:
        """Attach a long-lived cache header to fingerprinted assets."""
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = _IMMUTABLE
        return response


def mount_frontend(app: FastAPI) -> bool:
    """Mount the built interface, if there is one.

    Args:
        app: The application to mount onto.

    Returns:
        Whether a build was found. When it was not, the server still runs and serves
        its API; only the interface is missing.
    """
    index = WEB_ROOT / "index.html"
    if not index.is_file():
        _log.warning(
            "no built interface found; serving the API only. "
            "Run `npm --prefix frontend install && npm --prefix frontend run build`.",
            extra={"expected_at": str(WEB_ROOT)},
        )
        _install_missing_build_handler(app)
        return False

    assets = WEB_ROOT / "assets"
    if assets.is_dir():
        app.mount("/assets", _HashedAssets(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(request: Request, path: str) -> Response:
        """Serve a static file, or the application shell for a deep link."""
        if _is_server_path(path):
            return _api_not_found(request, path)

        candidate = (WEB_ROOT / path).resolve()
        # Containment check: a crafted path must not escape the web root. `resolve`
        # collapses `..` before the comparison, so this cannot be walked around.
        if path and candidate.is_file() and candidate.is_relative_to(WEB_ROOT):
            return FileResponse(candidate, headers={"Cache-Control": _NO_CACHE})

        return FileResponse(index, headers={"Cache-Control": _NO_CACHE})

    _log.info("serving the built interface", extra={"root": str(WEB_ROOT)})
    return True


def _install_missing_build_handler(app: FastAPI) -> None:
    """Explain the missing build at the root path instead of returning a bare 404."""

    @app.get("/", include_in_schema=False)
    async def no_build() -> Response:
        """Tell the operator how to build the interface."""
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": str(ErrorCode.NOT_FOUND),
                    "message": (
                        "The web interface has not been built. Run "
                        "'npm --prefix frontend install && npm --prefix frontend run build', "
                        "or use the container image, which ships it prebuilt."
                    ),
                    "retryable": False,
                }
            },
        )


def _is_server_path(path: str) -> bool:
    """Whether a path belongs to the API rather than the interface."""
    normalised = "/" + path.lstrip("/")
    return any(
        normalised == prefix or normalised.startswith(prefix + "/") for prefix in _API_PREFIXES
    )


def _api_not_found(request: Request, path: str) -> JSONResponse:
    """Return a JSON 404 for an unmatched server path."""
    del path
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": str(ErrorCode.NOT_FOUND),
                "message": "No such endpoint.",
                "retryable": False,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
