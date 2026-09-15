"""The tool-call loop through the **native Ollama adapter**, end to end.

``test_chat_tool_loop.py`` covers the same loop through the OpenAI-compatible
adapter. That left a real gap: every tool test ran against ``openai_compat``, so
nobody noticed that ``providers/ollama.py`` reported ``ToolSupport.NATIVE`` from
``/api/show`` and then never put ``tools`` on the wire — a model with genuine tool
support was silently never offered any, and answered "I cannot browse the web"
while the harness believed it had handed over a browse tool. This file is the
regression guard for that.
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.searxng import create_app as create_searxng
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import ProviderSettings, Settings


@pytest.fixture(scope="module")
def ollama_server() -> Iterator[FakeServer]:
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture(scope="module")
def searxng_server() -> Iterator[FakeServer]:
    with run_fake(create_searxng()) as server:
        yield server


@pytest.fixture
def client(settings: Settings, ollama_server: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        providers=ProviderSettings(ollama_hosts=(ollama_server.base_url,), autodiscover=False),
    )
    with TestClient(create_app(configured)) as test_client:
        yield test_client


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


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


def test_web_tools_reach_the_model_through_the_ollama_adapter(
    client: TestClient, registered: dict, searxng_server: FakeServer
) -> None:
    headers = _headers(registered)
    enabled = client.put(
        "/api/plugins/tools",
        headers=headers,
        json={"enabled": True, "base_url": searxng_server.base_url},
    )
    assert enabled.status_code == 200, enabled.text

    chat = client.post("/api/chats", headers=headers, json={"title": "Web"})
    chat_id = chat.json()["id"]

    payload = {
        "content": "what's the weather in Turin?",
        "model_ref": "ollama-0:tools",
        "web_tools": True,
    }
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [name for name, _ in events]
    assert "tool_call" in names, (
        "the Ollama adapter must send tools and surface the resulting call; "
        "if this fails, tools are being dropped on the way to /api/chat again"
    )
    assert "tool_result" in names
    assert names[-1] == "done"
