"""Tag CRUD and chat attachment over HTTP."""

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


def test_create_attach_filter_detach_delete(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)

    tag = client.post("/api/tags", headers=headers, json={"name": "starred", "color": "#f00"})
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["id"]

    chat = client.post("/api/chats", headers=headers, json={"title": "Chat A"})
    chat_id = chat.json()["id"]
    other_chat = client.post("/api/chats", headers=headers, json={"title": "Chat B"})

    attach = client.put(f"/api/tags/{tag_id}/chats/{chat_id}", headers=headers)
    assert attach.status_code == 204

    # Idempotent.
    assert client.put(f"/api/tags/{tag_id}/chats/{chat_id}", headers=headers).status_code == 204

    tagged = client.get(f"/api/tags/for-chat/{chat_id}", headers=headers)
    assert tagged.status_code == 200
    assert [t["name"] for t in tagged.json()] == ["starred"]

    filtered = client.get("/api/chats", headers=headers, params={"tag": tag_id})
    assert filtered.status_code == 200
    assert [c["id"] for c in filtered.json()["items"]] == [chat_id]

    chats_for_tag = client.get(f"/api/tags/{tag_id}/chats", headers=headers)
    assert chats_for_tag.status_code == 200
    assert chats_for_tag.json() == [chat_id]

    detach = client.delete(f"/api/tags/{tag_id}/chats/{chat_id}", headers=headers)
    assert detach.status_code == 204
    assert client.get(f"/api/tags/for-chat/{chat_id}", headers=headers).json() == []

    duplicate = client.post("/api/tags", headers=headers, json={"name": "starred"})
    assert duplicate.status_code == 409

    deleted = client.delete(f"/api/tags/{tag_id}", headers=headers)
    assert deleted.status_code == 204

    assert other_chat.status_code == 201


def test_attach_to_someone_elses_chat_is_not_found(
    client: TestClient, registered: dict
) -> None:
    owner_headers = _headers(registered)
    tag = client.post("/api/tags", headers=owner_headers, json={"name": "mine"})
    tag_id = tag.json()["id"]

    other = client.post(
        "/api/auth/register",
        json={
            "email": "guest@homelab.local",
            "password": "correct-horse-battery",
            "name": "Guest",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    other_chat = client.post("/api/chats", headers=other_headers, json={"title": "Not yours"})
    other_chat_id = other_chat.json()["id"]

    response = client.put(f"/api/tags/{tag_id}/chats/{other_chat_id}", headers=owner_headers)
    assert response.status_code == 404
