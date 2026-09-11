"""Health, readiness, version and metrics endpoints.

``/health`` and ``/ready`` answer different questions and must not be collapsed into
one. Liveness says the process is running and should not be restarted; it deliberately
touches nothing, so a slow database cannot trigger a restart loop. Readiness says the
instance can serve traffic, and for that the database has to answer.

Neither endpoint probes inference backends. A local Ollama host being switched off is a
normal state for this application, not an outage of it (ADR-0008).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from velox_ui import __version__
from velox_ui.api.deps import State
from velox_ui.clock import now_ms
from velox_ui.db.migrate import is_up_to_date
from velox_ui.db.repositories.users import UserRepository
from velox_ui.metrics import render

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Report that the process is alive.

    Returns:
        A constant payload. No dependency is contacted.
    """
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready(state: State, response: Response) -> dict[str, object]:
    """Report whether the instance can serve requests.

    Returns:
        The database state and, on failure, a 503 status so a load balancer stops
        sending traffic here.
    """
    try:
        async with state.db.session() as session:
            await session.execute(text("SELECT 1"))
        migrated = await is_up_to_date(state.db.engine)
    except Exception as exc:
        response.status_code = 503
        return {"status": "unavailable", "database": "unreachable", "detail": str(exc)[:200]}
    if not migrated:
        response.status_code = 503
        return {"status": "unavailable", "database": "ok", "schema": "migrations pending"}
    return {"status": "ready", "database": "ok", "schema": "current"}


@router.get("/api/version", summary="Build and feature information")
async def version(state: State) -> dict[str, object]:
    """Return version and enabled features, for the client bootstrap and for support."""
    return {
        "name": "velox-ui",
        "version": __version__,
        "uptime_ms": now_ms() - state.started_at_ms if state.started_at_ms else 0,
        "database": "sqlite" if state.settings.is_sqlite else "postgresql",
    }


@router.get("/api/config", summary="Client bootstrap configuration")
async def client_config(state: State) -> dict[str, object]:
    """Return what the frontend needs before a user is authenticated.

    Nothing here is sensitive: it is the shape of the login screen, not its contents.
    """
    settings = state.settings
    setup_required = False
    if settings.auth.enabled:
        async with state.db.session() as session:
            setup_required = await UserRepository(session).count() == 0
    return {
        "version": __version__,
        "auth": {
            "enabled": settings.auth.enabled,
            "open_registration": settings.auth.open_registration,
            # On a brand-new instance the client shows a "create the administrator
            # account" screen rather than a sign-in form.
            "setup_required": setup_required,
        },
        "features": {"metrics": settings.metrics.enabled},
    }


@router.get("/metrics", include_in_schema=False)
async def metrics(state: State) -> Response:
    """Expose Prometheus metrics.

    Returns:
        The exposition payload, or 404 when metrics are disabled so that a scrape
        against a disabled instance fails clearly instead of returning an empty body.
    """
    if not state.settings.metrics.enabled:
        return Response(status_code=404)
    return Response(content=render(), media_type="text/plain; version=0.0.4; charset=utf-8")
