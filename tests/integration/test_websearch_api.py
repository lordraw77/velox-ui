"""``POST /api/websearch`` against a fake SearXNG, disabled and enabled."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.fakes.searxng import create_app as create_searxng
from tests.fakes.server import FakeServer, run_fake


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


@pytest.fixture(scope="module")
def searxng_server() -> Iterator[FakeServer]:
    """A fake SearXNG backend."""
    with run_fake(create_searxng()) as server:
        yield server


def test_websearch_is_disabled_by_default(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/websearch", headers=_headers(registered), json={"query": "turin weather"}
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "unsupported_capability"


def test_websearch_returns_results_once_enabled(
    client: TestClient, registered: dict, searxng_server: FakeServer
) -> None:
    headers = _headers(registered)
    enabled = client.put(
        "/api/plugins/tools",
        headers=headers,
        json={"enabled": True, "base_url": searxng_server.base_url},
    )
    assert enabled.status_code == 200, enabled.text

    response = client.post("/api/websearch", headers=headers, json={"query": "turin weather"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "turin weather"
    assert "Result for turin weather" in body["results"]


def test_get_tools_lists_builtin_tools_once_enabled(
    client: TestClient, registered: dict, searxng_server: FakeServer
) -> None:
    headers = _headers(registered)

    before = client.get("/api/tools", headers=headers)
    assert before.json() == []

    enabled = client.put(
        "/api/plugins/tools",
        headers=headers,
        json={"enabled": True, "base_url": searxng_server.base_url},
    )
    assert enabled.status_code == 200, enabled.text

    after = client.get("/api/tools", headers=headers)
    names = {tool["name"] for tool in after.json()}
    assert names == {"web_search", "web_browse"}
    assert all(tool["server_id"] == "builtin" for tool in after.json())
