"""``HttpSseMcpClient`` against fake Streamable HTTP MCP servers.

The first fake is a small Starlette app driven through ``httpx.ASGITransport`` — no
socket, no network call — mirroring how ``tests/fakes/embedder.py`` and the RAG suite
keep things offline. The later tests need responses that stay open, which an
in-process transport cannot deliver, so they use ``tests/fakes/mcp_http.py`` on a
localhost socket.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from tests.fakes.mcp_http import Recorder, streamable_app
from tests.fakes.server import run_fake

from velox_ui.mcp.client import McpError, McpHttpStatusError, McpLegacySseEndpointError
from velox_ui.mcp.http_sse import HttpSseMcpClient


def _fake_mcp_app(*, required_token: str | None = None) -> Starlette:
    async def messages(request: Request) -> Response:
        if (
            required_token is not None
            and request.headers.get("authorization") != f"Bearer {required_token}"
        ):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": (await request.json()).get("id"),
                    "error": {"code": -32000, "message": "unauthorized"},
                },
                status_code=401,
            )
        body: dict[str, Any] = await request.json()
        method = body.get("method")
        request_id = body.get("id")

        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the input back.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ]
            }
        elif method == "tools/call":
            arguments = body["params"]["arguments"]
            result = {
                "content": [{"type": "text", "text": f"echo: {arguments.get('text', '')}"}],
                "isError": False,
            }
        else:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            )
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            headers={"Mcp-Session-Id": "session-abc"},
        )

    return Starlette(routes=[Route("/mcp", messages, methods=["POST"])])


def _client(app: Starlette, *, headers: dict[str, str] | None = None) -> HttpSseMcpClient:
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://mcp.test")
    return HttpSseMcpClient(url="http://mcp.test/mcp", headers=headers, client=http_client)


async def test_initialize_list_and_call_tool() -> None:
    client = _client(_fake_mcp_app())
    try:
        await client.initialize()
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["echo"]

        result = await client.call_tool("echo", {"text": "hi"})
        assert result.is_error is False
        assert result.content == "echo: hi"
    finally:
        await client.close()


async def test_missing_bearer_token_raises_mcp_error() -> None:
    client = _client(_fake_mcp_app(required_token="secret-token"))
    try:
        with pytest.raises(McpError):
            await client.initialize()
    finally:
        await client.close()


async def test_bearer_token_is_forwarded() -> None:
    client = _client(
        _fake_mcp_app(required_token="secret-token"),
        headers={"Authorization": "Bearer secret-token"},
    )
    try:
        await client.initialize()
        tools = await client.list_tools()
        assert len(tools) == 1
    finally:
        await client.close()


# -- Over a real socket: streams that stay open --------------------------------------
#
# The tests above go through httpx.ASGITransport, which buffers a response until its
# body ends. Everything below is about bodies that do not end, so it runs the fake on
# a real localhost port (tests/fakes/server.py).


async def test_an_event_stream_answer_is_found_among_noise_and_the_stream_left_open() -> None:
    recorder = Recorder()
    with run_fake(streamable_app(recorder, mode="stream")) as server:
        client = HttpSseMcpClient(url=f"{server.base_url}/mcp", timeout=5.0)
        try:
            started = time.monotonic()
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("echo", {"text": "hi"})
        finally:
            await client.close()
    assert [tool.name for tool in tools] == ["echo"]
    assert result.content == "echo: hi"
    # Each stream stays open after its answer; the client must not wait for it to end.
    assert time.monotonic() - started < 4.0


async def test_a_server_that_only_pings_hits_the_deadline_instead_of_hanging() -> None:
    recorder = Recorder()
    with run_fake(streamable_app(recorder, mode="silent")) as server:
        client = HttpSseMcpClient(url=f"{server.base_url}/mcp", timeout=0.5)
        started = time.monotonic()
        try:
            with pytest.raises(McpError, match=r"did not answer 'initialize' within 0\.5 s"):
                await client.initialize()
        finally:
            await client.close()
    # Pings every 50 ms would reset a per-read timeout forever; the deadline is overall.
    assert time.monotonic() - started < 3.0


async def test_a_legacy_endpoint_is_named_as_such_immediately() -> None:
    recorder = Recorder()
    with run_fake(streamable_app(recorder, mode="legacy")) as server:
        client = HttpSseMcpClient(url=f"{server.base_url}/mcp", timeout=30.0)
        started = time.monotonic()
        try:
            with pytest.raises(McpLegacySseEndpointError, match="legacy HTTP\\+SSE"):
                await client.initialize()
        finally:
            await client.close()
    assert time.monotonic() - started < 3.0


async def test_close_ends_the_server_session() -> None:
    recorder = Recorder()
    with run_fake(streamable_app(recorder, mode="json")) as server:
        client = HttpSseMcpClient(url=f"{server.base_url}/mcp")
        await client.initialize()
        await client.list_tools()
        await client.close()
        await client.close()  # safe twice, and the session is ended only once
    assert recorder.deleted_sessions == ["session-abc"]


async def test_an_error_status_names_the_url_and_keeps_the_status() -> None:
    recorder = Recorder()
    with run_fake(streamable_app(recorder, mode="unauthorized")) as server:
        url = f"{server.base_url}/mcp"
        client = HttpSseMcpClient(url=url)
        try:
            with pytest.raises(McpHttpStatusError) as caught:
                await client.initialize()
        finally:
            await client.close()
    assert caught.value.http_status == 401
    assert url in caught.value.message
