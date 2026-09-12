"""Serving the built interface.

The interesting cases here are not "does the page load" but the two ways a
single-page fallback goes wrong: swallowing API 404s so a client receives HTML where
it expected JSON, and serving files from outside the web root.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from velox_ui.api.static_files import WEB_ROOT

pytestmark = pytest.mark.skipif(
    not (WEB_ROOT / "index.html").is_file(),
    reason="the frontend has not been built in this checkout",
)


def test_root_serves_the_application_shell(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="app">' in response.text


def test_the_shell_is_never_cached(client: TestClient) -> None:
    # index.html names the fingerprinted assets. Caching it means a browser keeps
    # loading last week's application from this week's server.
    assert "no-cache" in client.get("/").headers["cache-control"]


def test_deep_links_return_the_shell(client: TestClient) -> None:
    # A conversation URL must load the app, not 404.
    response = client.get("/chat/01ABCDEF")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_hashed_assets_are_cached_for_a_long_time(client: TestClient) -> None:
    assets = sorted((WEB_ROOT / "assets").glob("index-*.js"))
    assert assets, "the build produced no entry script"
    response = client.get(f"/assets/{assets[0].name}")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_unknown_api_paths_stay_json(client: TestClient) -> None:
    # The fallback must not swallow these: a client parsing JSON would receive an
    # HTML document and a 200 where it expected a typed 404.
    for path in ("/api/nope", "/v1/nope", "/api/chats/x/nope"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"].startswith("application/json"), path
        assert response.json()["error"]["code"] == "not_found"


def test_health_endpoints_are_not_shadowed(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["status"] == "ready"


def test_path_traversal_does_not_escape_the_web_root(client: TestClient) -> None:
    # A crafted path must never reach the database, the secret key, or source code.
    for path in (
        "/../velox.db",
        "/../../pyproject.toml",
        "/assets/../../secret.key",
        "/%2e%2e%2fsecret.key",
    ):
        response = client.get(path)
        # Either refused outright, or answered with the shell. Never with a file from
        # outside the web root.
        assert response.status_code in (200, 400, 404), path
        if response.status_code == 200:
            assert "sqlite" not in response.text.lower(), path
            assert "[project]" not in response.text, path
