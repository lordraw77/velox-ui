"""The full tool-call loop through ``POST /api/chats/{id}/completions``.

Wires a real MCP server (the ``tests/fakes/mcp_stdio_server.py`` subprocess, exactly
like ``test_mcp_api.py``) and a real fake OpenAI-compatible backend (``model=tools``,
``tests/fakes/openai_compat.py``) together through a custom model, and drives one
full turn: the model calls a tool, the turn executes it against the real MCP server,
feeds the result back, and the model answers using it — asserting
``tool_call`` -> ``tool_result`` -> continued generation -> ``done``, end to end.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.openai_compat import create_app as create_openai
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import EndpointSettings, ProviderSettings, Settings


@pytest.fixture(scope="module")
def openai_server() -> Iterator[FakeServer]:
    with run_fake(create_openai()) as server:
        yield server


@pytest.fixture
def client(settings: Settings, openai_server: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        providers=ProviderSettings(
            endpoints=(
                EndpointSettings(preset="lmstudio", base_url=f"{openai_server.base_url}/v1"),
            ),
            autodiscover=False,
        ),
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


def _register_and_connect_mcp_server(client: TestClient, headers: dict) -> tuple[str, str]:
    created = client.post(
        "/api/mcp/servers",
        headers=headers,
        json={
            "name": "weather",
            "transport": "stdio",
            "config": {
                "command": sys.executable,
                "args": ["-m", "tests.fakes.mcp_stdio_server"],
            },
            "approval": "never",
        },
    )
    assert created.status_code == 201, created.text
    server_id = created.json()["id"]

    connected = client.post(f"/api/mcp/servers/{server_id}/connect", headers=headers)
    assert connected.status_code == 200, connected.text
    return server_id, created.json()["name"]


def test_tool_call_loop_calls_tool_then_answers(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    server_id, server_name = _register_and_connect_mcp_server(client, headers)

    chat = client.post("/api/chats", headers=headers, json={"title": "Weather"})
    assert chat.status_code == 201
    chat_id = chat.json()["id"]

    payload = {
        "content": "What's the weather in Turin?",
        "model_ref": "lmstudio-0:tools",
        "tool_server_ids": [server_id],
    }
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [name for name, _ in events]
    assert "tool_call" in names
    assert "tool_result" in names
    assert names[-1] == "done"
    assert names.count("delta") > 0  # the follow-up answer streamed as ordinary text

    tool_call = next(payload for name, payload in events if name == "tool_call")
    assert tool_call["name"] == f"{server_name}__get_weather"
    assert tool_call["args"] == {"city": "Turin"}

    tool_result = next(payload for name, payload in events if name == "tool_result")
    assert tool_result["ok"] is True
    assert "Turin" in tool_result["content"]

    done = next(payload for name, payload in events if name == "done")
    assert done["finish_reason"] == "stop"


def test_no_tool_server_ids_means_no_tool_events(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    chat = client.post("/api/chats", headers=headers, json={"title": "Plain"})
    chat_id = chat.json()["id"]

    payload = {"content": "hello", "model_ref": "lmstudio-0:tools"}
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [name for name, _ in events]
    assert "tool_call" not in names
    assert "tool_result" not in names
