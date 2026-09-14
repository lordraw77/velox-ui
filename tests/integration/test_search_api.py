"""Search over HTTP: the real migration path (alembic upgrade), not create_all."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def test_search_finds_titles_and_messages(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    chat = client.post("/api/chats", headers=headers, json={"title": "Quarterly roadmap"})
    assert chat.status_code == 201

    response = client.get("/api/search", headers=headers, params={"q": "roadmap"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["kind"] == "chat_title"
    assert body["items"][0]["chat_id"] == chat.json()["id"]


def test_search_requires_a_query(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    response = client.get("/api/search", headers=headers, params={"q": ""})
    assert response.status_code == 422


def test_search_is_scoped_per_user(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    response = client.get("/api/search", headers=headers, params={"q": "nothing to find"})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_unauthenticated_is_rejected(client: TestClient) -> None:
    assert client.get("/api/search", params={"q": "x"}).status_code == 401
