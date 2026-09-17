"""``SseMcpClient``, the legacy HTTP+SSE transport, against a fake on a real socket.

Real sockets, not ``httpx.ASGITransport``: the transport is built on a stream that
stays open for the client's whole life, which an in-process transport would buffer
until it ended — that is, forever.
"""

from __future__ import annotations

import time

import pytest
from tests.fakes.mcp_http import Recorder, legacy_sse_app
from tests.fakes.server import run_fake

from velox_ui.mcp.client import McpError, McpHttpStatusError
from velox_ui.mcp.sse import SseMcpClient


async def test_initialize_list_and_call_tool() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder)) as server:
        client = SseMcpClient(url=f"{server.base_url}/sse", timeout=5.0)
        try:
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("echo", {"text": "hi"})
        finally:
            await client.close()
    assert [tool.name for tool in tools] == ["echo"]
    assert result.content == "echo: hi"
    assert result.is_error is False
    # Every message went to the announced endpoint; responses came back on the stream,
    # each preceded by a notification the client had to skip.
    assert [path for _, path, _ in recorder.posts()] == ["/messages/"] * 4


async def test_bearer_token_reaches_the_stream_and_every_message() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder)) as server:
        client = SseMcpClient(
            url=f"{server.base_url}/sse", headers={"Authorization": "Bearer secret-token"}
        )
        try:
            await client.initialize()
            await client.list_tools()
        finally:
            await client.close()
    assert recorder.requests
    assert {auth for _, _, auth in recorder.requests} == {"Bearer secret-token"}


async def test_an_endpoint_on_another_origin_is_refused_before_anything_is_sent() -> None:
    recorder = Recorder()
    app = legacy_sse_app(
        recorder, endpoint="http://attacker.invalid/steal?session_id={session}"
    )
    with run_fake(app) as server:
        client = SseMcpClient(
            url=f"{server.base_url}/sse", headers={"Authorization": "Bearer secret-token"}
        )
        try:
            with pytest.raises(McpError, match="another origin"):
                await client.initialize()
        finally:
            await client.close()
    # Only the stream was opened. No message, and so no credential, went anywhere else.
    assert [method for method, _, _ in recorder.requests] == ["GET"]


async def test_a_server_that_never_announces_an_endpoint_times_out_clearly() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder, announce=False)) as server:
        client = SseMcpClient(url=f"{server.base_url}/sse", endpoint_timeout=0.5)
        started = time.monotonic()
        try:
            with pytest.raises(McpError, match="did not announce a message endpoint"):
                await client.initialize()
        finally:
            await client.close()
    assert time.monotonic() - started < 3.0


async def test_a_stream_that_ends_fails_the_pending_request_at_once() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder, close_after_endpoint=True)) as server:
        client = SseMcpClient(url=f"{server.base_url}/sse", timeout=30.0)
        started = time.monotonic()
        try:
            with pytest.raises(McpError, match="closed its event stream"):
                await client.initialize()
        finally:
            await client.close()
    # Not the 30 s request deadline: nothing can arrive on a stream that has ended.
    assert time.monotonic() - started < 5.0


async def test_a_response_in_the_post_body_is_accepted_too() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder, answer_inline=True)) as server:
        client = SseMcpClient(url=f"{server.base_url}/sse", timeout=5.0)
        try:
            await client.initialize()
            tools = await client.list_tools()
        finally:
            await client.close()
    assert [tool.name for tool in tools] == ["echo"]


async def test_an_error_status_on_the_stream_keeps_the_status() -> None:
    recorder = Recorder()
    with run_fake(legacy_sse_app(recorder)) as server:
        client = SseMcpClient(url=f"{server.base_url}/not-here")
        try:
            with pytest.raises(McpHttpStatusError) as caught:
                await client.initialize()
        finally:
            await client.close()
    assert caught.value.http_status == 404


async def test_close_is_safe_before_initialize_and_twice() -> None:
    client = SseMcpClient(url="http://127.0.0.1:9/sse")
    await client.close()
    await client.close()
