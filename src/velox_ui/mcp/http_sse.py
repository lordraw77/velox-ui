"""MCP over Streamable HTTP.

The MCP spec had two HTTP transports: the original "HTTP+SSE" (a ``GET`` opening a
long-lived event stream, requests going out over separate ``POST``s) was superseded by
"Streamable HTTP" in the 2025-03-26 revision of the spec, which folds both directions
onto a single endpoint — the server answers each ``POST`` either with a plain JSON
response or with a ``text/event-stream`` body carrying one or more JSON-RPC messages.
This module speaks Streamable HTTP; the older transport is :mod:`velox_ui.mcp.sse`,
and :mod:`velox_ui.mcp.http_auto` picks between them (ADR-0022). The module keeps the
name ``http_sse`` from the design doc, and the stored transport value ``http_sse``
keeps meaning Streamable HTTP, so existing servers are unaffected.

velox-ui's two operations (list tools, call a tool) are both request/response, so
this client sends ``Accept: application/json, text/event-stream`` and, for an event
stream, reads events as they arrive until the one answering its request — skipping
notifications and server-to-client requests on the way — then closes the stream. It
never reads a stream to its end: a server may keep one open, and a legacy endpoint
reached by mistake keeps one open forever, pinging often enough that httpx's per-read
timeout never fires. Every request has an overall deadline instead.

A session the server opened is ended with ``DELETE`` on close, as the spec asks. Every
velox-ui connection lives for one operation, so without it a stateful server — the
MCP gateway image runs stateful — would keep one session, and often one server
process, alive per connect and per tool call until its idle timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from velox_ui.mcp.client import (
    MCP_PROTOCOL_VERSION,
    McpError,
    McpHttpStatusError,
    McpLegacySseEndpointError,
    McpTool,
    McpToolResult,
    next_request_id,
)
from velox_ui.mcp.sse_events import iter_sse_events, parse_json_rpc

__all__ = ["HttpSseMcpClient", "default_http_client"]

_log = logging.getLogger("velox.mcp.http_sse")

_ACCEPT = "application/json, text/event-stream"
_SESSION_HEADER = "Mcp-Session-Id"

# The same budget the stdio transport gives one call (mcp/stdio.py).
_REQUEST_TIMEOUT_S = 60.0
# Notifications and the closing DELETE are courtesies; they must not hold anything up.
_COURTESY_TIMEOUT_S = 5.0


def default_http_client() -> httpx.AsyncClient:
    """Build the short-lived client an HTTP transport uses when none is injected.

    No read timeout: an event stream legitimately goes quiet between events, and a
    per-read timeout is also what keep-alive pings defeat. Each request's overall
    deadline is enforced by the transport instead.
    """
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))


class HttpSseMcpClient:
    """An MCP client transport that talks Streamable HTTP to a server.

    Args:
        url: The server's MCP endpoint.
        headers: Extra headers, typically including a decrypted bearer token — never
            persisted; callers build this from ``mcp_server.config`` plus a secret
            looked up separately.
        client: An ``httpx.AsyncClient`` to use. Tests inject one; transport detection
            shares one between the two HTTP transports. Production otherwise builds a
            short-lived client, since MCP connections are infrequent, cold-path
            operations, not a hot-path resource worth pooling process-wide.
        timeout: Overall deadline for one request, response included.
    """

    __slots__ = ("_client", "_headers", "_owns_client", "_session_id", "_timeout", "_url")

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _REQUEST_TIMEOUT_S,
    ) -> None:
        """Store the endpoint; no connection is opened yet."""
        self._url = url
        self._headers = headers or {}
        self._owns_client = client is None
        self._client = client or default_http_client()
        self._session_id: str | None = None
        self._timeout = timeout

    async def initialize(self) -> None:
        """Perform the MCP handshake and capture the session id, if the server sends one.

        Raises:
            McpLegacySseEndpointError: The URL is a legacy HTTP+SSE endpoint.
            McpHttpStatusError: The endpoint answered with an error status.
            McpError: Any other transport or protocol failure.
        """
        await self._post(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "velox-ui", "version": "1"},
            },
        )
        await self._notify("notifications/initialized", {})

    async def list_tools(self) -> list[McpTool]:
        """Return the server's tool list."""
        result = await self._post("tools/list", {})
        return [
            McpTool(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )
            for tool in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Call one tool and return its result."""
        result = await self._post("tools/call", {"name": name, "arguments": arguments})
        text_parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return McpToolResult(
            content="\n".join(text_parts), is_error=bool(result.get("isError", False))
        )

    async def close(self) -> None:
        """End the server-side session, if there is one, then release the client."""
        session_id, self._session_id = self._session_id, None
        if session_id is not None:
            headers = {**self._headers, _SESSION_HEADER: session_id}
            # 405 is a valid answer: the server does not let clients end sessions.
            with contextlib.suppress(httpx.HTTPError, TimeoutError):
                async with asyncio.timeout(_COURTESY_TIMEOUT_S):
                    await self._client.delete(self._url, headers=headers)
        if self._owns_client:
            await self._client.aclose()

    def _request_headers(self) -> dict[str, str]:
        headers = {**self._headers, "Content-Type": "application/json", "Accept": _ACCEPT}
        if self._session_id is not None:
            headers[_SESSION_HEADER] = self._session_id
        return headers

    def _remember_session(self, response: httpx.Response) -> None:
        session_id = response.headers.get(_SESSION_HEADER)
        if session_id:
            self._session_id = session_id

    async def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = next_request_id()
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream(
                    "POST", self._url, json=payload, headers=self._request_headers()
                ) as response:
                    self._remember_session(response)
                    if response.status_code >= 400:
                        raise McpHttpStatusError(
                            f"MCP server at {self._url} returned HTTP {response.status_code}.",
                            http_status=response.status_code,
                        )
                    message = await _read_response(response, request_id, self._url)
        except TimeoutError as exc:
            raise McpError(
                f"MCP server at {self._url} did not answer {method!r} "
                f"within {self._timeout:g} s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach the MCP server at {self._url}: {exc}") from exc

        if "error" in message:
            error = message["error"]
            raise McpError(f"MCP server error: {error.get('message', 'unknown error')}")
        result: dict[str, Any] = message.get("result", {})
        return result

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        # A notification has no response to wait for: best-effort, and the body --
        # usually empty with 202 -- is never read.
        with contextlib.suppress(httpx.HTTPError, TimeoutError):
            async with asyncio.timeout(_COURTESY_TIMEOUT_S):
                async with self._client.stream(
                    "POST", self._url, json=payload, headers=self._request_headers()
                ) as response:
                    self._remember_session(response)


async def _read_response(response: httpx.Response, request_id: int, url: str) -> dict[str, Any]:
    """Extract the JSON-RPC response to ``request_id`` from a JSON or SSE body."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        message = parse_json_rpc(await response.aread())
        if message is None:
            raise McpError(f"MCP server at {url} sent no JSON-RPC response.")
        return message

    first = True
    async for event in iter_sse_events(response.aiter_lines()):
        if first and event.event == "endpoint":
            raise McpLegacySseEndpointError(
                f"{url} is a legacy HTTP+SSE MCP endpoint: it announced a message "
                "endpoint instead of answering. Use the 'sse' transport, or 'http_auto' "
                "to detect it."
            )
        first = False
        message = parse_json_rpc(event.data)
        if message is None:
            _log.warning("non-JSON SSE event from MCP server, ignored")
            continue
        if message.get("id") == request_id and ("result" in message or "error" in message):
            return message
        # A notification, a server-to-client request, or someone else's response.
    raise McpError(f"MCP server at {url} closed the stream without answering.")
