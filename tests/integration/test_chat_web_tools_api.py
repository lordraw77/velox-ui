"""The tool-call loop with a builtin (plugin-backed) tool, not an MCP server.

Mirrors ``test_chat_tool_loop.py``'s MCP version: a fake OpenAI-compatible backend
(``model=tools``) calls whatever tool it was offered; here that's the builtin
``web_search`` tool from the enabled ``"tools"`` plugin (a fake SearXNG), not an MCP
server — proving the loop dispatches a builtin tool without touching ``state.mcp`` at
all, and that no approval gate applies (``"approval": "auto"`` always).
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.openai_compat import create_app as create_openai
from tests.fakes.searxng import create_app as create_searxng
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import EndpointSettings, ProviderSettings, Settings


@pytest.fixture(scope="module")
def openai_server() -> Iterator[FakeServer]:
    with run_fake(create_openai()) as server:
        yield server


@pytest.fixture(scope="module")
def searxng_server() -> Iterator[FakeServer]:
    with run_fake(create_searxng()) as server:
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


def _enable_tools_plugin(client: TestClient, headers: dict, searxng_server: FakeServer) -> None:
    enabled = client.put(
        "/api/plugins/tools",
        headers=headers,
        json={"enabled": True, "base_url": searxng_server.base_url},
    )
    assert enabled.status_code == 200, enabled.text


def test_web_tool_call_runs_without_approval_and_without_mcp(
    client: TestClient, registered: dict, searxng_server: FakeServer
) -> None:
    headers = _headers(registered)
    _enable_tools_plugin(client, headers, searxng_server)

    chat = client.post("/api/chats", headers=headers, json={"title": "Web"})
    assert chat.status_code == 201
    chat_id = chat.json()["id"]

    payload = {
        "content": "What's the weather in Turin?",
        "model_ref": "lmstudio-0:tools",
        "web_tools": True,
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

    tool_call = next(payload for name, payload in events if name == "tool_call")
    # The fake backend calls whichever tool it was offered first; any builtin one
    # proves the point here, which is that it dispatched with no approval gate
    # and without going near the MCP manager.
    assert tool_call["name"] in {"current_datetime", "web_search", "web_browse"}
    assert tool_call["approval"] == "auto"

    tool_result = next(payload for name, payload in events if name == "tool_result")
    assert tool_result["ok"] is True


def test_web_tools_false_means_no_tool_events(
    client: TestClient, registered: dict, searxng_server: FakeServer
) -> None:
    headers = _headers(registered)
    _enable_tools_plugin(client, headers, searxng_server)

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


def test_web_tools_true_but_plugin_disabled_means_no_tool_events(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    chat = client.post("/api/chats", headers=headers, json={"title": "Disabled"})
    chat_id = chat.json()["id"]

    payload = {"content": "hello", "model_ref": "lmstudio-0:tools", "web_tools": True}
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [name for name, _ in events]
    assert "tool_call" not in names
