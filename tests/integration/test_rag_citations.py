"""Chat streaming with a knowledge collection attached: citation events and context
injection (docs/design/03-http-api.md, ``event: citation``).

Reuses the fake Ollama backend from ``test_chat_stream.py`` and the deterministic fake
embedder, so this stays fully offline.
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.rag.embedders import registry as embedder_registry
from velox_ui.settings import ProviderSettings, Settings


@pytest.fixture(scope="module")
def ollama() -> Iterator[FakeServer]:
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolve(embedder_ref: str, state: object, *, dim: int) -> FakeEmbedder:
        del state
        return FakeEmbedder(dim=dim, ref=embedder_ref)

    monkeypatch.setattr(embedder_registry, "resolve_embedder", _resolve)


@pytest.fixture
def client(settings: Settings, ollama: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        providers=ProviderSettings(ollama_hosts=(ollama.base_url,), autodiscover=False),
    )
    with TestClient(create_app(configured)) as test_client:
        yield test_client


def _parse_sse(lines: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name: str | None = None
    for line in lines:
        if line.startswith(":") or not line.strip():
            continue
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
        elif line.startswith("data: ") and event_name:
            events.append((event_name, msgspec.json.decode(line[len("data: ") :])))
    return events


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def _ingest_ready_collection(client: TestClient, headers: dict) -> str:
    import time

    created = client.post(
        "/api/collections",
        headers=headers,
        json={"name": "KB", "embedder_ref": "fake:test", "dim": 16},
    )
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]

    upload = client.post(
        "/api/files",
        headers=headers,
        files={
            "upload": ("notes.txt", b"velox-ui streams tokens with low overhead.", "text/plain")
        },
    )
    assert upload.status_code == 201
    file_id = upload.json()["id"]

    queued = client.post(
        f"/api/collections/{collection_id}/documents",
        headers=headers,
        data={"file_id": file_id},
    )
    assert queued.status_code == 202
    document_id = queued.json()["document"]["id"]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        docs = client.get(f"/api/collections/{collection_id}/documents", headers=headers).json()
        status = next(d["status"] for d in docs if d["id"] == document_id)
        if status == "ready":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("document never became ready")
    return collection_id


def test_completion_with_knowledge_ids_emits_citations(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    collection_id = _ingest_ready_collection(client, headers)

    chat = client.post("/api/chats", headers=headers, json={"title": "RAG"})
    assert chat.status_code == 201
    chat_id = chat.json()["id"]

    payload = {
        "content": "How does velox-ui stream tokens?",
        "model_ref": "ollama-0:llama3.2",
        "knowledge_ids": [collection_id],
    }
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [name for name, _ in events]
    assert names[0] == "start"
    citations = [payload for name, payload in events if name == "citation"]
    assert len(citations) >= 1
    assert set(citations[0]) == {"chunk_id", "document_id", "locator"}


def test_completion_without_knowledge_ids_emits_no_citations(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    chat = client.post("/api/chats", headers=headers, json={"title": "Plain"})
    chat_id = chat.json()["id"]

    payload = {"content": "hello", "model_ref": "ollama-0:llama3.2"}
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    assert all(name != "citation" for name, _ in events)
