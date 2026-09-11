"""The msgspec/Pydantic boundary from ADR-0001.

FastAPI was chosen over Litestar for its ecosystem, on the condition that Pydantic
never runs on the latency-critical path. That condition is a convention, and
conventions erode: someone adds a ``response_model`` to a streaming route because every
other route has one, and the 15 ms budget quietly goes with it.

This test is the guard. It is written now, in phase 1, before the routes it protects
exist, so the rule is enforced from the first line of the streaming path rather than
retrofitted after a regression.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from velox_ui.app import create_app
from velox_ui.settings import Settings

# Routes reachable during a completion turn or a hot list read. Paths are matched by
# prefix. Extend this as the phases land; never shrink it to make a test pass.
HOT_PATH_PREFIXES = (
    "/api/chats",
    "/api/compare",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
    "/api/models",
)

# Read endpoints that may return a Pydantic body despite sitting under a hot prefix,
# because they are cold in practice. Each entry needs a reason.
EXEMPT: dict[str, str] = {}


def _all_routes(app: FastAPI) -> list[APIRoute]:
    """Collect every APIRoute, including those inside lazily included routers."""
    found: list[APIRoute] = []
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            pending.extend(nested.routes)
    return found


def test_hot_routes_declare_no_response_model(settings: Settings) -> None:
    app = create_app(settings)
    offenders = [
        route.path
        for route in _all_routes(app)
        if route.path.startswith(HOT_PATH_PREFIXES)
        and route.response_model is not None
        and route.path not in EXEMPT
    ]
    assert not offenders, (
        "these hot-path routes declare a response_model, which puts Pydantic "
        f"serialization in the completion path (ADR-0001): {offenders}"
    )


def test_the_guard_can_actually_see_routes(settings: Settings) -> None:
    # A guard that silently inspects an empty list passes forever. This asserts the
    # traversal reaches the routes that do exist today.
    paths = {route.path for route in _all_routes(create_app(settings))}
    assert "/api/auth/login" in paths
    assert "/health" in paths
