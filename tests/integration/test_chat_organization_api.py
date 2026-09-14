"""Pin, archive, rename and move a conversation, over HTTP (``PATCH /api/chats/{id}``)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def test_pin_archive_rename_and_move(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    chat = client.post("/api/chats", headers=headers, json={"title": "Original"})
    chat_id = chat.json()["id"]
    folder = client.post("/api/folders", headers=headers, json={"name": "Inbox"})
    folder_id = folder.json()["id"]

    pinned = client.patch(f"/api/chats/{chat_id}", headers=headers, json={"pinned": True})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["pinned"] is True
    assert pinned.json()["archived"] is False
    assert pinned.json()["title"] == "Original"

    moved = client.patch(
        f"/api/chats/{chat_id}",
        headers=headers,
        json={"title": "Renamed", "folder_id": folder_id},
    )
    assert moved.status_code == 200
    assert moved.json()["title"] == "Renamed"
    assert moved.json()["folder_id"] == folder_id
    # Untouched fields survive a partial patch.
    assert moved.json()["pinned"] is True

    back_to_root = client.patch(
        f"/api/chats/{chat_id}", headers=headers, json={"folder_id": None}
    )
    assert back_to_root.status_code == 200
    assert back_to_root.json()["folder_id"] is None

    archived = client.patch(f"/api/chats/{chat_id}", headers=headers, json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

    listed_default = client.get("/api/chats", headers=headers)
    assert chat_id not in [c["id"] for c in listed_default.json()["items"]]

    listed_archived = client.get("/api/chats", headers=headers, params={"archived": "true"})
    assert chat_id in [c["id"] for c in listed_archived.json()["items"]]


def test_patch_unknown_chat_is_not_found(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    response = client.patch("/api/chats/does-not-exist", headers=headers, json={"pinned": True})
    assert response.status_code == 404


def test_create_chat_with_custom_model_id(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    persona = client.post(
        "/api/custom-models",
        headers=headers,
        json={"slug": "research-buddy", "name": "Research Buddy", "system_prompt": "Be terse."},
    )
    assert persona.status_code == 201, persona.text
    model_id = persona.json()["id"]

    chat = client.post(
        "/api/chats",
        headers=headers,
        json={"title": "From persona", "custom_model_id": model_id},
    )
    assert chat.status_code == 201
    assert chat.json()["custom_model_id"] == model_id

    fetched = client.get(f"/api/chats/{chat.json()['id']}", headers=headers)
    assert fetched.json()["custom_model_id"] == model_id
