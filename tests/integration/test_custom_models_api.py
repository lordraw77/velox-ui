"""Custom model (persona) CRUD over HTTP."""

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


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def test_create_get_update_delete(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)

    created = client.post(
        "/api/custom-models",
        headers=headers,
        json={
            "slug": "concise-coder",
            "name": "Concise Coder",
            "system_prompt": "Answer in code only.",
            "params": {"temperature": 0.1},
            "fallback_chain": [{"provider_id": "ollama-0", "model_key": "llama3.2"}],
            "visibility": "private",
        },
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert created.json()["fallback_chain"] == [
        {"provider_id": "ollama-0", "model_key": "llama3.2"}
    ]

    listed = client.get("/api/custom-models", headers=headers)
    assert listed.status_code == 200
    assert [m["slug"] for m in listed.json()] == ["concise-coder"]

    fetched = client.get(f"/api/custom-models/{model_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Concise Coder"

    updated = client.patch(
        f"/api/custom-models/{model_id}", headers=headers, json={"name": "Renamed"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"

    duplicate = client.post(
        "/api/custom-models", headers=headers, json={"slug": "concise-coder", "name": "Dup"}
    )
    assert duplicate.status_code == 409

    deleted = client.delete(f"/api/custom-models/{model_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/custom-models/{model_id}", headers=headers).status_code == 404


def test_tools_field_round_trips(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/custom-models",
        headers=headers,
        json={"slug": "with-tools", "name": "With Tools", "tools": ["srv-1", "srv-2"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["tools"] == ["srv-1", "srv-2"]

    model_id = created.json()["id"]
    updated = client.patch(
        f"/api/custom-models/{model_id}", headers=headers, json={"tools": ["srv-3"]}
    )
    assert updated.status_code == 200
    assert updated.json()["tools"] == ["srv-3"]

    fetched = client.get(f"/api/custom-models/{model_id}", headers=headers)
    assert fetched.json()["tools"] == ["srv-3"]


def test_invalid_slug_is_rejected(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    response = client.post(
        "/api/custom-models", headers=headers, json={"slug": "Not Valid!", "name": "X"}
    )
    assert response.status_code == 422


def test_private_model_is_hidden_from_other_users(client: TestClient, registered: dict) -> None:
    owner_headers = _headers(registered)
    created = client.post(
        "/api/custom-models", headers=owner_headers, json={"slug": "secret", "name": "Secret"}
    )
    model_id = created.json()["id"]

    other = client.post(
        "/api/auth/register",
        json={
            "email": "guest@homelab.local",
            "password": "correct-horse-battery",
            "name": "Guest",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    listed = client.get("/api/custom-models", headers=other_headers)
    assert listed.json() == []

    fetched = client.get(f"/api/custom-models/{model_id}", headers=other_headers)
    assert fetched.status_code == 403

    hijack = client.patch(
        f"/api/custom-models/{model_id}", headers=other_headers, json={"name": "Hijacked"}
    )
    assert hijack.status_code == 404
