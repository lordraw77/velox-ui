"""Local model management and paged conversations, over HTTP."""

from __future__ import annotations

import time
from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.openai_compat import create_app as create_openai
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import AuthSettings, EndpointSettings, ProviderSettings, Settings


@pytest.fixture(scope="module")
def ollama() -> Iterator[FakeServer]:
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture(scope="module")
def compat() -> Iterator[FakeServer]:
    with run_fake(create_openai()) as server:
        yield server


@pytest.fixture
def client(settings: Settings, ollama: FakeServer, compat: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        auth=AuthSettings(
            open_registration=True, access_token_ttl_s=60, refresh_token_ttl_s=3600
        ),
        providers=ProviderSettings(
            ollama_hosts=(ollama.base_url,),
            endpoints=(EndpointSettings(preset="lmstudio", base_url=f"{compat.base_url}/v1"),),
            autodiscover=False,
        ),
    )
    with TestClient(create_app(configured)) as test_client:
        yield test_client


@pytest.fixture
def admin(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def _events(response) -> list[tuple[str, dict]]:
    events, name = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            events.append((name, msgspec.json.decode(line[6:])))
    return events


def test_pull_streams_progress_then_the_outcome(client: TestClient, admin: dict) -> None:
    with client.stream(
        "POST", "/api/providers/ollama-0/local/pull", headers=admin, json={"name": "qwen3:1.7b"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["x-velox-job"]
        events = _events(response)

    assert events[-1][0] == "done"
    final = events[-1][1]
    assert final["state"] == "succeeded"
    assert (final["completed_bytes"], final["total_bytes"]) == (400, 400)


def test_a_failed_pull_reports_a_typed_error(client: TestClient, admin: dict) -> None:
    with client.stream(
        "POST", "/api/providers/ollama-0/local/pull", headers=admin, json={"name": "ghost"}
    ) as response:
        final = _events(response)[-1][1]
    assert final["state"] == "failed"
    assert final["error"]["code"] == "model_not_found"


def test_a_detached_pull_keeps_running_and_can_be_cancelled(
    client: TestClient, admin: dict
) -> None:
    started = client.post(
        "/api/providers/ollama-0/local/pull?detach=true",
        headers=admin,
        json={"name": "slowpull"},
    )
    assert started.status_code == 202
    job_id = started.json()["id"]

    # Starting the same download again joins it instead of beginning a second one.
    again = client.post(
        "/api/providers/ollama-0/local/pull?detach=true",
        headers=admin,
        json={"name": "slowpull"},
    )
    assert again.json()["id"] == job_id

    listed = client.get("/api/model-jobs", headers=admin).json()["jobs"]
    assert listed[0]["id"] == job_id and listed[0]["state"] == "running"

    assert client.delete(f"/api/model-jobs/{job_id}", headers=admin).status_code == 204
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get("/api/model-jobs", headers=admin).json()["jobs"][0]["state"]
        if state != "running":
            break
        time.sleep(0.05)
    assert state == "cancelled"


def test_create_accepts_a_modelfile(client: TestClient, admin: dict) -> None:
    with client.stream(
        "POST",
        "/api/providers/ollama-0/local/create",
        headers=admin,
        json={"name": "terse", "modelfile": "FROM llama3.2\nSYSTEM Be brief."},
    ) as response:
        assert _events(response)[-1][1]["state"] == "succeeded"

    refused = client.post(
        "/api/providers/ollama-0/local/create",
        headers=admin,
        json={"name": "bad", "modelfile": "FROM ./weights.gguf"},
    )
    assert refused.status_code == 422


def test_inspection_routes(client: TestClient, admin: dict) -> None:
    installed = client.get("/api/providers/ollama-0/local/models", headers=admin).json()[
        "models"
    ]
    assert installed[0]["name"] == "llama3.2"

    details = client.get("/api/providers/ollama-0/local/models/llama3.2", headers=admin).json()
    assert details["parameters"]["stop"] == ["<|im_start|>", "<|im_end|>"]

    running = client.get("/api/providers/ollama-0/local/running", headers=admin).json()[
        "models"
    ]
    assert running[0]["context_length"] == 4096

    assert (
        client.post(
            "/api/providers/ollama-0/local/unload", headers=admin, json={"name": "llama3.2"}
        ).status_code
        == 204
    )
    assert (
        client.delete("/api/providers/ollama-0/local/models/ghost", headers=admin).status_code
        == 404
    )


def test_names_with_tags_and_namespaces_are_accepted(client: TestClient, admin: dict) -> None:
    response = client.get(
        "/api/providers/ollama-0/local/models/hf.co/bartowski/Llama-3.2-1B-GGUF:Q4_K_M",
        headers=admin,
    )
    assert response.status_code == 200


def test_backends_without_the_capability_say_so(client: TestClient, admin: dict) -> None:
    response = client.post(
        "/api/providers/lmstudio-0/local/pull", headers=admin, json={"name": "qwen3"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_capability"
    groups = {
        group["provider_id"]: group
        for group in client.get("/api/models", headers=admin).json()["providers"]
    }
    assert groups["lmstudio-0"]["features"] == []
    assert "pull" in groups["ollama-0"]["features"]


def test_management_requires_an_administrator(client: TestClient, admin: dict) -> None:
    member = client.post(
        "/api/auth/register",
        json={"email": "guest@homelab.local", "password": "correct-horse-battery", "name": "G"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {member}"}
    assert (
        client.post(
            "/api/providers/ollama-0/local/pull", headers=headers, json={"name": "qwen3"}
        ).status_code
        == 403
    )
    assert (
        client.delete(
            "/api/providers/ollama-0/local/models/llama3.2", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get("/api/providers/ollama-0/local/running", headers=headers).status_code == 200
    )


def test_opening_a_conversation_returns_one_page(client: TestClient, admin: dict) -> None:
    chat_id = client.post("/api/chats", headers=admin, json={"title": "Paged"}).json()["id"]
    for _ in range(3):
        with client.stream(
            "POST",
            f"/api/chats/{chat_id}/completions",
            headers=admin,
            json={"content": "hello", "model_ref": "ollama-0:llama3.2"},
        ) as response:
            list(response.iter_lines())

    opened = client.get(f"/api/chats/{chat_id}?limit=4", headers=admin).json()
    assert len(opened["messages"]) == 4
    assert opened["messages_cursor"] is not None

    older = client.get(
        f"/api/chats/{chat_id}/messages",
        headers=admin,
        params={"cursor": opened["messages_cursor"], "limit": 4},
    ).json()
    assert len(older["items"]) == 2 and older["next_cursor"] is None
    roles = [message["role"] for message in older["items"] + opened["messages"]]
    assert roles == ["user", "assistant"] * 3

    assert (
        client.get(
            f"/api/chats/{chat_id}/messages", headers=admin, params={"cursor": "garbage!"}
        ).status_code
        == 422
    )
