"""``StdioMcpClient`` against a real subprocess (``tests/fakes/mcp_stdio_server.py``).

Fully offline: the "server" is a local Python script talking JSON-RPC over its own
stdin/stdout, never a network call.
"""

from __future__ import annotations

import sys

import pytest

from velox_ui.mcp.client import McpError
from velox_ui.mcp.stdio import StdioMcpClient


def _client() -> StdioMcpClient:
    return StdioMcpClient(command=sys.executable, args=["-m", "tests.fakes.mcp_stdio_server"])


async def test_initialize_and_list_tools() -> None:
    client = _client()
    try:
        await client.initialize()
        tools = await client.list_tools()
    finally:
        await client.close()

    assert len(tools) == 1
    assert tools[0].name == "get_weather"
    assert tools[0].input_schema["type"] == "object"


async def test_call_tool_success() -> None:
    client = _client()
    try:
        await client.initialize()
        result = await client.call_tool("get_weather", {"city": "Turin"})
    finally:
        await client.close()

    assert result.is_error is False
    assert "Turin" in result.content


async def test_call_tool_reports_error_without_raising() -> None:
    client = _client()
    try:
        await client.initialize()
        result = await client.call_tool("no_such_tool", {})
    finally:
        await client.close()

    assert result.is_error is True


async def test_initialize_fails_fast_on_a_bad_command() -> None:
    client = StdioMcpClient(command="__velox_definitely_not_a_real_binary__")
    with pytest.raises((McpError, OSError)):
        await client.initialize()
    await client.close()
