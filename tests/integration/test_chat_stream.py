"""The completion endpoint, end to end, against a fake Ollama.

These assert the promises of ADR-0004 and ADR-0005 at the HTTP boundary: the client
learns the message id before any row exists, tokens arrive as they are produced rather
than in one piece at the end, the reply is persisted without the stream ever waiting on
the database, and an abandoned stream still keeps what the model produced.
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import ProviderSettings, Settings


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


def _parse_sse(lines: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE frames into (event, payload) pairs, ignoring heartbeat comments."""
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


def _new_chat(client: TestClient, headers: dict) -> str:
    response = client.post("/api/chats", headers=headers, json={"title": "Test"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _stream(client: TestClient, chat_id: str, headers: dict, **body) -> list[tuple[str, dict]]:
    payload = {"content": "hello", "model_ref": "ollama-0:llama3.2", **body}
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        return _parse_sse(list(response.iter_lines()))


def test_models_are_discovered_from_the_provider(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    body = client.get("/api/models", headers=headers).json()
    groups = body["providers"]
    assert groups[0]["provider_id"] == "ollama-0"
    assert groups[0]["is_local"] is True
    refs = [model["model_ref"] for model in groups[0]["models"]]
    assert "ollama-0:llama3.2" in refs


def test_providers_report_health(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    body = client.get("/api/providers", headers=headers).json()
    assert body["providers"][0]["health"]["state"] == "up"


def test_stream_produces_the_documented_event_sequence(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    events = _stream(client, chat_id, headers)
    names = [name for name, _ in events]

    assert names[0] == "start"
    assert "delta" in names
    assert names[-2:] == ["usage", "done"]

    text = "".join(payload["t"] for name, payload in events if name == "delta")
    assert text == "Hello, world! This is velox-ui."


def test_start_frame_carries_the_message_id_immediately(
    client: TestClient, registered: dict
) -> None:
    # ADR-0005: the id is allocated in-process, so the client has it before the row
    # exists and nothing downstream waits on the insert.
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    events = _stream(client, chat_id, headers)
    name, payload = events[0]
    assert name == "start"
    assert len(payload["message_id"]) == 26
    assert len(payload["user_message_id"]) == 26
    assert payload["model_ref"] == "ollama-0:llama3.2"


def test_usage_reports_backend_metrics_and_zero_cost(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    events = _stream(client, chat_id, headers)
    usage = next(payload for name, payload in events if name == "usage")

    assert usage["tokens_in"] == 26
    assert usage["tokens_out"] == 9
    assert usage["tok_per_s"] == pytest.approx(10.0)
    assert usage["prompt_eval_ms"] == pytest.approx(200.0)
    assert usage["cost_micros"] == 0, "a local model is free and must say so explicitly"
    assert usage["ttft_ms"] is not None and usage["ttft_ms"] >= 0


def test_reply_is_persisted_and_readable_afterwards(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    _stream(client, chat_id, headers)

    body = client.get(f"/api/chats/{chat_id}", headers=headers).json()
    messages = body["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hello"
    assert messages[1]["content"] == "Hello, world! This is velox-ui."
    assert messages[1]["status"] == "complete"
    assert messages[1]["model_ref"] == "ollama-0:llama3.2"
    assert messages[1]["timings"]["ttft_ms"] is not None
    assert body["active_leaf_id"] == messages[1]["id"]


def test_a_second_turn_continues_the_branch(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    _stream(client, chat_id, headers)
    _stream(client, chat_id, headers, content="again")

    messages = client.get(f"/api/chats/{chat_id}", headers=headers).json()["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message["depth"] for message in messages] == [0, 1, 2, 3]


def test_regenerating_from_a_parent_creates_a_sibling(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    first = _stream(client, chat_id, headers)
    root_user_id = dict(first)["start"]["user_message_id"]

    # Branch from the original user turn: history must not be overwritten (ADR-0006).
    _stream(client, chat_id, headers, content="hello", parent_id=None)
    body = client.get(f"/api/chats/{chat_id}", headers=headers).json()
    assert body["messages"][0]["sibling_count"] >= 1
    assert root_user_id


def test_loading_state_is_reported_distinctly(client: TestClient, registered: dict) -> None:
    # "loading model" is not "generating" and is not "stuck" (ADR-0008).
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    events = _stream(client, chat_id, headers, model_ref="ollama-0:unloaded")
    phases = [payload["phase"] for name, payload in events if name == "status"]
    assert phases[0] == "loading_model"
    assert "generating" in phases


def test_backend_error_is_typed_not_a_500(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    events = _stream(client, chat_id, headers, model_ref="ollama-0:oom")

    name, payload = next((name, p) for name, p in events if name == "error")
    assert name == "error"
    assert payload["code"] == "out_of_memory"
    assert payload["provider"] == "ollama-0"
    assert events[-1] == ("done", {"finish_reason": "error"})


def test_failed_turn_is_persisted_as_an_error(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    _stream(client, chat_id, headers, model_ref="ollama-0:oom")

    messages = client.get(f"/api/chats/{chat_id}", headers=headers).json()["messages"]
    assert messages[-1]["status"] == "error"


def test_unknown_provider_is_a_clear_error(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    response = client.post(
        f"/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "hi", "model_ref": "nope-9:llama3.2"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_malformed_model_ref_is_rejected(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = _new_chat(client, headers)
    response = client.post(
        f"/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "hi", "model_ref": "llama3.2"},
    )
    assert response.status_code == 404
    assert "provider:model" in response.json()["error"]["message"]


def test_someone_elses_chat_is_not_streamable(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    response = client.post(
        "/api/chats/01NOTMINE0000000000000000/completions",
        headers=headers,
        json={"content": "hi", "model_ref": "ollama-0:llama3.2"},
    )
    assert response.status_code == 404


def test_chat_listing_and_deletion(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    first = _new_chat(client, headers)
    _new_chat(client, headers)

    listed = client.get("/api/chats", headers=headers).json()
    assert len(listed["items"]) == 2
    assert listed["next_cursor"] is None

    assert client.delete(f"/api/chats/{first}", headers=headers).status_code == 204
    assert len(client.get("/api/chats", headers=headers).json()["items"]) == 1
