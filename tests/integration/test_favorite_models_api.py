"""Starring models over HTTP."""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient

from velox_ui.app import create_app
from velox_ui.settings import AuthSettings, Settings


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        auth=AuthSettings(
            open_registration=True, access_token_ttl_s=60, refresh_token_ttl_s=3600
        ),
    )
    with TestClient(create_app(configured)) as test_client:
        yield test_client


def test_star_and_unstar(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}

    assert client.get("/api/favorite-models", headers=headers).json() == {"model_refs": []}

    first = client.put(
        "/api/favorite-models",
        headers=headers,
        json={"model_ref": "ollama:llama3", "favorite": True},
    )
    assert first.status_code == 200, first.text
    again = client.put(
        "/api/favorite-models",
        headers=headers,
        json={"model_ref": "ollama:llama3", "favorite": True},
    )
    assert again.json() == {"model_refs": ["ollama:llama3"]}

    client.put(
        "/api/favorite-models", headers=headers, json={"model_ref": "x:y", "favorite": True}
    )
    removed = client.put(
        "/api/favorite-models",
        headers=headers,
        json={"model_ref": "ollama:llama3", "favorite": False},
    )
    assert removed.json() == {"model_refs": ["x:y"]}


def test_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/favorite-models").status_code == 401
