"""``HttpSseMcpClient`` against an in-process fake Streamable HTTP MCP server.

The fake is a small Starlette app driven through ``httpx.ASGITransport`` — no socket,
no network call — mirroring how ``tests/fakes/embedder.py`` and the RAG suite keep
things offline.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from velox_ui.mcp.client import McpError
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
