"""Folder CRUD over HTTP."""

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


def test_create_list_rename_move_delete(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)

    created = client.post("/api/folders", headers=headers, json={"name": "Work"})
    assert created.status_code == 201, created.text
    root = created.json()

    child = client.post(
        "/api/folders", headers=headers, json={"name": "Deep dive", "parent_id": root["id"]}
    )
    assert child.status_code == 201, child.text
    child_id = child.json()["id"]

    listed = client.get("/api/folders", headers=headers)
    assert listed.status_code == 200
    names = {folder["name"] for folder in listed.json()}
    assert names == {"Work", "Deep dive"}

    renamed = client.patch(
        f"/api/folders/{root['id']}", headers=headers, json={"name": "Work renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Work renamed"

    moved = client.put(
        f"/api/folders/{child_id}/move",
        headers=headers,
        json={"parent_id": None, "sort_order": 3},
    )
    assert moved.status_code == 200
    assert moved.json()["parent_id"] is None
    assert moved.json()["sort_order"] == 3

    deleted = client.delete(f"/api/folders/{root['id']}", headers=headers)
    assert deleted.status_code == 204


def test_parent_must_belong_to_caller(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    response = client.post(
        "/api/folders", headers=headers, json={"name": "Orphan", "parent_id": "does-not-exist"}
    )
    assert response.status_code == 404


def test_unauthenticated_is_rejected(client: TestClient) -> None:
    assert client.get("/api/folders").status_code == 401


def test_rename_someone_elses_folder_is_not_found(client: TestClient, registered: dict) -> None:
    owner_headers = _headers(registered)
    created = client.post("/api/folders", headers=owner_headers, json={"name": "Private"})
    folder_id = created.json()["id"]

    other = client.post(
        "/api/auth/register",
        json={
            "email": "guest@homelab.local",
            "password": "correct-horse-battery",
            "name": "Guest",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    response = client.patch(
        f"/api/folders/{folder_id}", headers=other_headers, json={"name": "Stolen"}
    )
    assert response.status_code == 404
