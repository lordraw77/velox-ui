"""Chat memory over HTTP: measuring the context, compacting, editing, and sending it.

The fake Ollama records every request, so the last assertions check what actually
reached the backend after a compaction, not what the service believes it sent.
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes import ollama as fake_ollama
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import ProviderSettings, Settings

MODEL = "ollama-0:llama3.2"


@pytest.fixture(scope="module")
def ollama() -> Iterator[FakeServer]:
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture
def client(settings: Settings, ollama: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        providers=ProviderSettings(ollama_hosts=(ollama.base_url,), autodiscover=False),
    )
    with TestClient(create_app(configured)) as test_client:
        yield test_client


def _chat_requests() -> list[dict]:
    return [body for path, body in fake_ollama.REQUESTS if path == "/api/chat"]


def _say(client: TestClient, chat_id: str, headers: dict, text: str) -> None:
    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": text, "model_ref": MODEL},
    ) as response:
        assert response.status_code == 200, response.read()
        list(response.iter_lines())


def _conversation(client: TestClient, headers: dict, turns: int) -> str:
    chat_id = client.post("/api/chats", headers=headers, json={"title": "T"}).json()["id"]
    for index in range(turns):
        _say(client, chat_id, headers, f"question {index}")
    return chat_id


def test_context_reports_the_conversation(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _conversation(client, headers, turns=2)

    usage = client.get(
        f"/api/chats/{chat_id}/context", headers=headers, params={"model_ref": MODEL}
    )
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["messages_total"] == 4
    assert body["messages_in_context"] == 4
    assert body["messages_summarized"] == 0
    assert body["used"] > 0


def test_compact_stores_memory_and_later_turns_send_it(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _conversation(client, headers, turns=3)

    compacted = client.post(
        f"/api/chats/{chat_id}/compact", headers=headers, json={"model_ref": MODEL}
    )
    assert compacted.status_code == 200, compacted.text
    result = compacted.json()
    assert result["memory"]
    assert result["summarized"] == 4  # six messages, the last two kept verbatim
    assert result["omitted"] == 0

    stored = client.get(f"/api/chats/{chat_id}", headers=headers).json()
    assert stored["memory"] == result["memory"]
    assert stored["memory_upto_id"] == result["memory_upto_id"]
    assert stored["message_count"] == 6  # nothing was deleted

    usage = client.get(f"/api/chats/{chat_id}/context", headers=headers).json()
    assert usage["messages_summarized"] == 4
    assert usage["messages_in_context"] == 2
    assert usage["memory_tokens"] > 0

    _say(client, chat_id, headers, "and now?")
    sent = _chat_requests()[-1]["messages"]
    assert sent[0]["role"] == "system"
    assert result["memory"] in sent[0]["content"]
    contents = [message["content"] for message in sent]
    assert "question 0" not in contents
    assert "question 2" in contents and contents[-1] == "and now?"


def test_compact_with_nothing_to_summarize_is_refused(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _conversation(client, headers, turns=1)
    refused = client.post(
        f"/api/chats/{chat_id}/compact", headers=headers, json={"model_ref": MODEL}
    )
    assert refused.status_code == 422, refused.text


def test_memory_can_be_edited_and_forgotten(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _conversation(client, headers, turns=3)
    first = client.post(
        f"/api/chats/{chat_id}/compact", headers=headers, json={"model_ref": MODEL}
    ).json()

    edited = client.put(
        f"/api/chats/{chat_id}/memory", headers=headers, json={"memory": "my own notes"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json() == {
        "memory": "my own notes",
        "memory_upto_id": first["memory_upto_id"],
    }

    forgotten = client.put(f"/api/chats/{chat_id}/memory", headers=headers, json={"memory": ""})
    assert forgotten.json() == {"memory": None, "memory_upto_id": None}
    usage = client.get(f"/api/chats/{chat_id}/context", headers=headers).json()
    assert usage["messages_in_context"] == 6


def test_another_users_chat_is_not_found(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    assert client.get("/api/chats/nope/context", headers=headers).status_code == 404
    assert (
        client.put("/api/chats/nope/memory", headers=headers, json={"memory": "x"}).status_code
        == 404
    )
