"""``POST /api/images/generate`` against a fake OpenAI-compatible backend."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.fakes.media import create_app as create_media
from tests.fakes.server import FakeServer, run_fake


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


@pytest.fixture(scope="module")
def media_server() -> Iterator[FakeServer]:
    """A fake image-generation backend."""
    with run_fake(create_media()) as server:
        yield server


def test_generate_is_disabled_by_default(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/images/generate", headers=_headers(registered), json={"prompt": "a cat"}
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "unsupported_capability"


def test_generate_stores_and_returns_files(
    client: TestClient, registered: dict, media_server: FakeServer
) -> None:
    headers = _headers(registered)
    enabled = client.put(
        "/api/plugins/images",
        headers=headers,
        json={"enabled": True, "base_url": media_server.base_url},
    )
    assert enabled.status_code == 200, enabled.text

    generated = client.post(
        "/api/images/generate", headers=headers, json={"prompt": "a cat", "n": 2}
    )
    assert generated.status_code == 200, generated.text
    images = generated.json()["images"]
    assert len(images) == 2

    content = client.get(f"/api/files/{images[0]['file_id']}/content", headers=headers)
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/png"


def test_generate_maps_backend_failure_to_upstream_error(
    client: TestClient, registered: dict, media_server: FakeServer
) -> None:
    headers = _headers(registered)
    client.put(
        "/api/plugins/images",
        headers=headers,
        json={"enabled": True, "base_url": media_server.base_url},
    )
    response = client.post("/api/images/generate", headers=headers, json={"prompt": "fail"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
