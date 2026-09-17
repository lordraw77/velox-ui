"""Fake MCP servers for both HTTP transports, meant for ``tests.fakes.server.run_fake``.

They run over a real socket because the behaviour under test is about streams that
stay open: an in-process ASGI transport buffers a response until its body ends, and a
body that never ends would hang the test rather than exercise the client.

Each fake answers the same three methods with the same results, so a test can swap
transports without rewriting its assertions, and records what reached it so a test
can assert on what did not.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

__all__ = ["Recorder", "legacy_sse_app", "streamable_app"]

# Long enough to outlast any test; the client closing the stream ends it sooner.
_FOREVER_S = 3600.0


@dataclass
class Recorder:
    """What reached a fake: request methods, paths and authorization headers."""

    requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    deleted_sessions: list[str] = field(default_factory=list)

    def note(self, request: Request) -> None:
        self.requests.append(
            (request.method, request.url.path, request.headers.get("authorization"))
        )

    def posts(self) -> list[tuple[str, str, str | None]]:
        return [entry for entry in self.requests if entry[0] == "POST"]


def _answer(body: dict[str, Any]) -> dict[str, Any] | None:
    """The response a well-behaved server gives, or ``None`` for a notification."""
    method = body.get("method")
    if "id" not in body:
        return None
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
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
        text = body["params"]["arguments"].get("text", "")
        result = {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False}
    else:
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {"code": -32601, "message": "method not found"},
        }
    return {"jsonrpc": "2.0", "id": body["id"], "result": result}


def _event(message: dict[str, Any], *, name: str = "message") -> bytes:
    return f"event: {name}\ndata: {json.dumps(message)}\n\n".encode()


_NOISE_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}


def streamable_app(recorder: Recorder, *, mode: str = "json") -> Starlette:
    """A Streamable HTTP server at ``/mcp``.

    Args:
        recorder: Collects what reached the server.
        mode: How requests are answered.
            ``"json"`` -- a plain JSON body.
            ``"stream"`` -- an event stream that sends a ping, a notification and a
            response to some other request before the real response, then stays open.
            ``"silent"`` -- an event stream of pings that never answers.
            ``"legacy"`` -- what the Python MCP SDK's legacy ``/sse`` route does with a
            POST: 200, an ``endpoint`` event, then pings forever.
            ``"unauthorized"`` -- 401 for everything.
    """

    async def mcp(request: Request) -> Response:
        recorder.note(request)
        if mode == "unauthorized":
            return Response(status_code=401)
        if request.method == "DELETE":
            recorder.deleted_sessions.append(request.headers.get("mcp-session-id", ""))
            return Response(status_code=200)

        body: dict[str, Any] = await request.json()
        answer = _answer(body)
        if answer is None:
            return Response(status_code=202)
        headers = {"Mcp-Session-Id": "session-abc"}

        if mode == "json":
            return JSONResponse(answer, headers=headers)

        async def events() -> AsyncIterator[bytes]:
            if mode == "legacy":
                yield b"event: endpoint\ndata: /messages/?session_id=abc\n\n"
            if mode == "stream":
                yield b": ping\n\n"
                yield _event(_NOISE_NOTIFICATION)
                yield _event({"jsonrpc": "2.0", "id": -1, "result": {}})
                yield _event(answer)
            while True:
                yield b": ping\n\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(events(), media_type="text/event-stream", headers=headers)

    return Starlette(routes=[Route("/mcp", mcp, methods=["POST", "DELETE"])])


def legacy_sse_app(
    recorder: Recorder,
    *,
    endpoint: str = "/messages/?session_id={session}",
    announce: bool = True,
    close_after_endpoint: bool = False,
    answer_inline: bool = False,
    answer_post_to_sse: bool = False,
) -> Starlette:
    """A legacy HTTP+SSE server: ``GET /sse`` for the stream, ``POST /messages/``.

    Args:
        recorder: Collects what reached the server.
        endpoint: The endpoint announced; ``{session}`` is replaced with the session id.
        announce: Whether to send the ``endpoint`` event at all.
        close_after_endpoint: End the stream right after announcing.
        answer_inline: Answer in the POST body instead of on the stream.
        answer_post_to_sse: Treat ``POST /sse`` like ``GET /sse``, as the Python MCP SDK
            does, instead of answering 405.
    """
    queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    async def sse(request: Request) -> Response:
        recorder.note(request)
        session = uuid.uuid4().hex
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        queues[session] = queue

        async def events() -> AsyncIterator[bytes]:
            if announce:
                yield f"event: endpoint\ndata: {endpoint.format(session=session)}\n\n".encode()
            if close_after_endpoint:
                return
            yield b": ping\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=_FOREVER_S)
                except TimeoutError:
                    return
                yield _event(_NOISE_NOTIFICATION)
                yield _event(message)

        return StreamingResponse(events(), media_type="text/event-stream")

    async def messages(request: Request) -> Response:
        recorder.note(request)
        session = request.query_params.get("session_id", "")
        queue = queues.get(session)
        if queue is None:
            return Response(status_code=404)
        answer = _answer(await request.json())
        if answer is None:
            return Response(status_code=202)
        if answer_inline:
            return JSONResponse(answer)
        await queue.put(answer)
        return Response(status_code=202)

    sse_methods = ["GET", "POST"] if answer_post_to_sse else ["GET"]
    return Starlette(
        routes=[
            Route("/sse", sse, methods=sse_methods),
            Route("/messages/", messages, methods=["POST"]),
        ]
    )
