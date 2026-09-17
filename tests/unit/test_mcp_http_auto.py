"""``AutoHttpMcpClient``: which transport it settles on, and when it refuses to guess."""

from __future__ import annotations

import pytest
from tests.fakes.mcp_http import Recorder, legacy_sse_app, streamable_app
from tests.fakes.server import run_fake

from velox_ui.mcp.client import McpError, McpHttpStatusError
from velox_ui.mcp.http_auto import AutoHttpMcpClient


async def _connect(url: str) -> tuple[str | None, list[str]]:
    client = AutoHttpMcpClient(url=url)
    try:
        await client.initialize()
        tools = await client.list_tools()
        return client.transport, [tool.name for tool in tools]
    finally:
        await client.close()


async def test_a_streamable_server_is_used_as_such() -> None:
    recorder = Recorder()
    with run_fake(streamable_app(recorder, mode="stream")) as server:
        transport, tools = await _connect(f"{server.base_url}/mcp")
    assert transport == "http_sse"
    assert tools == ["echo"]
    # Nothing was tried over the legacy transport.
    assert all(method != "GET" for method, _, _ in recorder.requests)


async def test_a_legacy_server_refusing_post_falls_back_as_the_spec_describes() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder)) as server:  # POST /sse -> 405
        transport, tools = await _connect(f"{server.base_url}/sse")
    assert transport == "sse"
    assert tools == ["echo"]


async def test_a_legacy_server_answering_post_with_its_endpoint_falls_back_too() -> None:
    # The Python MCP SDK's /sse route: POST gets 200 and the endpoint event, not a 4xx.
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder, answer_post_to_sse=True)) as server:
        transport, tools = await _connect(f"{server.base_url}/sse")
    assert transport == "sse"
    assert tools == ["echo"]


async def test_a_rejected_credential_is_reported_not_retried_as_the_other_transport() -> None:
    recorder = Recorder()
    with (
        run_fake(streamable_app(recorder, mode="unauthorized")) as server,
        pytest.raises(McpHttpStatusError) as caught,
    ):
        await _connect(f"{server.base_url}/mcp")
    assert caught.value.http_status == 401
    assert [method for method, _, _ in recorder.requests] == ["POST"]


async def test_a_url_that_speaks_neither_names_both_failures() -> None:
    recorder = Recorder()
    with (
        run_fake(streamable_app(recorder, mode="json")) as server,
        pytest.raises(McpError, match="speaks neither HTTP transport") as caught,
    ):
        await _connect(f"{server.base_url}/nowhere")
    assert "Streamable HTTP: it answered HTTP 404" in caught.value.message
    assert "HTTP+SSE:" in caught.value.message


async def test_calls_before_initialize_fail_cleanly() -> None:
    client = AutoHttpMcpClient(url="http://127.0.0.1:9/mcp")
    try:
        with pytest.raises(McpError, match="not connected"):
            await client.list_tools()
    finally:
        await client.close()
