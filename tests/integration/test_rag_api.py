"""Collections, document ingestion and retrieval over HTTP.

The embedder is monkeypatched to the deterministic :class:`FakeEmbedder`
(``tests/fakes/embedder.py``): no test in this suite downloads a real ONNX model or
calls a real backend (docs/design/00-overview.md).
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.embedder import FakeEmbedder

from velox_ui.app import create_app
from velox_ui.rag.embedders import registry as embedder_registry
from velox_ui.settings import AuthSettings, Settings


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolve(embedder_ref: str, state: object, *, dim: int) -> FakeEmbedder:
        del state
        return FakeEmbedder(dim=dim, ref=embedder_ref)

    monkeypatch.setattr(embedder_registry, "resolve_embedder", _resolve)


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


def _wait_ready(
    client: TestClient, headers: dict, collection_id: str, document_id: str
) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/collections/{collection_id}/documents", headers=headers)
        assert response.status_code == 200
        for document in response.json():
            if document["id"] == document_id:
                if document["status"] in ("ready", "failed"):
                    return document
                break
        time.sleep(0.05)
    raise AssertionError("document never reached a terminal status")


def test_collection_document_ingest_and_query(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)

    created = client.post(
        "/api/collections",
        headers=headers,
        json={
            "name": "Handbook",
            "embedder_ref": "fake:test",
            "dim": 16,
            "max_tokens": 200,
            "overlap_tokens": 20,
        },
    )
    assert created.status_code == 201, created.text
    collection = created.json()
    assert collection["dim"] == 16
    assert collection["embedder_ref"] == "fake:test"

    listed = client.get("/api/collections", headers=headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [collection["id"]]

    upload = client.post(
        "/api/files",
        headers=headers,
        files={
            "upload": ("notes.txt", b"The velox-ui project ships a fast chat UI.", "text/plain")
        },
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["id"]

    queued = client.post(
        f"/api/collections/{collection['id']}/documents",
        headers=headers,
        data={"file_id": file_id, "title": "Notes"},
    )
    assert queued.status_code == 202, queued.text
    document = queued.json()["document"]
    job_id = queued.json()["job_id"]
    assert document["status"] == "pending"

    ready = _wait_ready(client, headers, collection["id"], document["id"])
    assert ready["status"] == "ready", ready
    assert ready["chunk_count"] >= 1

    job_events = client.get(f"/api/rag-jobs/{job_id}/events", headers=headers)
    assert job_events.status_code == 200

    query = client.post(
        f"/api/collections/{collection['id']}/query",
        headers=headers,
        json={"query": "fast chat UI", "k": 3},
    )
    assert query.status_code == 200, query.text
    hits = query.json()["items"]
    assert len(hits) >= 1
    assert hits[0]["document_id"] == document["id"]
    assert "velox-ui" in hits[0]["content"]

    deleted_doc = client.delete(f"/api/documents/{document['id']}", headers=headers)
    assert deleted_doc.status_code == 204

    empty_query = client.post(
        f"/api/collections/{collection['id']}/query",
        headers=headers,
        json={"query": "fast chat UI", "k": 3},
    )
    assert empty_query.json()["items"] == []

    deleted_collection = client.delete(f"/api/collections/{collection['id']}", headers=headers)
    assert deleted_collection.status_code == 204

    missing = client.get(f"/api/collections/{collection['id']}", headers=headers)
    assert missing.status_code == 404


def test_collection_visibility_is_enforced(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/collections",
        headers=headers,
        json={"name": "Private", "embedder_ref": "fake:test", "dim": 16},
    )
    assert created.status_code == 201
    collection_id = created.json()["id"]

    other = client.post(
        "/api/auth/register",
        json={
            "email": "second@homelab.local",
            "password": "correct-horse-battery",
            "name": "Two",
        },
    )
    assert other.status_code == 200
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    forbidden = client.get(f"/api/collections/{collection_id}", headers=other_headers)
    assert forbidden.status_code == 403


def test_websearch_is_a_typed_stub(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/websearch", headers=_headers(registered), json={"query": "anything"}
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "unsupported_capability"


def test_unknown_embedder_model_is_rejected(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/collections",
        headers=_headers(registered),
        json={"name": "Bad", "embedder_ref": "fastembed:not-a-real-model"},
    )
    assert response.status_code == 422
