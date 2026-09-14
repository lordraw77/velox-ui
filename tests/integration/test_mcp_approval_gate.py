"""The approval gate: ``event: tool_call`` carries ``approval: required``, the turn
blocks on ``POST /api/tools/approve``, and rejection is fed back to the model as an
unsuccessful tool result rather than the turn failing.

Runs the streaming turn on a background thread while the main thread polls for the
pending approval and resolves it via a second, ordinary HTTP request — the same shape
a second browser tab clicking "reject" would produce.
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


def _register_and_connect(client: TestClient, headers: dict, *, approval: str) -> str:
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
            "approval": approval,
        },
    )
    assert created.status_code == 201, created.text
    server_id = created.json()["id"]
    connected = client.post(f"/api/mcp/servers/{server_id}/connect", headers=headers)
    assert connected.status_code == 200, connected.text
    return server_id


def test_approval_required_call_waits_then_rejection_is_fed_back(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    server_id = _register_and_connect(client, headers, approval="always")

    chat = client.post("/api/chats", headers=headers, json={"title": "Weather"})
    chat_id = chat.json()["id"]
    payload = {
        "content": "weather?",
        "model_ref": "lmstudio-0:tools",
        "tool_server_ids": [server_id],
    }

    def run_stream() -> list[tuple[str, dict]]:
        with client.stream(
            "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
        ) as response:
            assert response.status_code == 200
            return _parse_sse(list(response.iter_lines()))

    import threading

    result: dict[str, list[tuple[str, dict]]] = {}

    def _target() -> None:
        result["events"] = run_stream()

    thread = threading.Thread(target=_target)
    thread.start()

    # Poll for the pending call to appear, then reject it. The MCP manager holds
    # pending approvals in process memory (``McpManager._pending``), so a plain
    # `requests`-style poll from this thread is exactly what a second browser tab
    # would do against `/api/tools/approve`.
    call_id: str | None = None
    import time

    start = time.monotonic()
    while time.monotonic() - start < 10:
        # We cannot see the SSE stream from this thread without consuming it, so
        # instead poll the app's in-memory MCP manager for a pending call directly.
        pending = client.app.state.velox.mcp._pending
        if pending:
            call_id = next(iter(pending))
            break
        time.sleep(0.02)

    assert call_id is not None, "tool call never became pending"
    approved = client.post(
        "/api/tools/approve",
        headers=headers,
        json={"call_id": call_id, "approved": False},
    )
    assert approved.status_code == 200
    assert approved.json() == {"ok": True}

    thread.join(timeout=10)
    events = result["events"]
    names = [name for name, _ in events]
    assert "tool_call" in names
    tool_call = next(payload for name, payload in events if name == "tool_call")
    assert tool_call["approval"] == "required"

    tool_result = next(payload for name, payload in events if name == "tool_result")
    assert tool_result["ok"] is False
    assert names[-1] == "done"
