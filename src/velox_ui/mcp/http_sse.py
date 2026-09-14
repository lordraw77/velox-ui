"""MCP over Streamable HTTP.

The MCP spec had two HTTP transports: the original "HTTP+SSE" (a ``GET`` opening a
long-lived event stream, requests going out over separate ``POST``s) was superseded by
"Streamable HTTP" in the 2025-03-26 revision of the spec, which folds both directions
onto a single ``POST /messages`` endpoint — the server answers either with a plain JSON
response or, for requests that may want to stream, with a ``text/event-stream`` body
carrying one or more JSON-RPC messages. Streamable HTTP is what current MCP servers
implement, so that is what this module speaks; the name of the module keeps the
familiar "http_sse" term from the design doc since the streaming *response* shape is
still SSE-framed, only the transport-level handshake changed.

velox-ui's two operations (list tools, call a tool) are both simple request/response,
so this client always sends ``Accept: application/json, text/event-stream`` and reads
either a single JSON body or the first complete JSON-RPC message off an SSE response —
it does not need a standalone server-initiated notification stream (a separate ``GET``
in the spec), which nothing in this phase's scope uses.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import httpx

from velox_ui.mcp.client import (
    MCP_PROTOCOL_VERSION,
    McpError,
    McpTool,
    McpToolResult,
    next_request_id,
)

__all__ = ["HttpSseMcpClient"]

_log = logging.getLogger("velox.mcp.http_sse")

_ACCEPT = "application/json, text/event-stream"
_SESSION_HEADER = "Mcp-Session-Id"


class HttpSseMcpClient:
    """An MCP client transport that talks Streamable HTTP to a server.

    Args:
        url: The server's message endpoint.
        headers: Extra headers, typically including a decrypted bearer token — never
            persisted; callers build this from ``mcp_server.config`` plus a secret
            looked up separately.
        client: An ``httpx.AsyncClient`` to use. Tests inject one built on
            ``httpx.ASGITransport`` against an in-process fake server; production
            builds a short-lived client since MCP server connections are infrequent,
            cold-path operations (connect, occasional tool calls), not a hot-path
            resource worth pooling process-wide like the provider HTTP client.
    """

    __slots__ = ("_client", "_headers", "_owns_client", "_session_id", "_url")

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store the endpoint; no connection is opened yet."""
        self._url = url
        self._headers = headers or {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._session_id: str | None = None

    async def initialize(self) -> None:
        """Perform the MCP handshake and capture the session id, if the server sends one."""
        response = await self._post(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "velox-ui", "version": "1"},
            },
        )
        del response
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
        """Close the HTTP client, if this instance owns it."""
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = next_request_id()
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        headers = {**self._headers, "Content-Type": "application/json", "Accept": _ACCEPT}
        if self._session_id is not None:
            headers[_SESSION_HEADER] = self._session_id
        try:
            response = await self._client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach the MCP server: {exc}") from exc

        if response.status_code >= 400:
            raise McpError(f"MCP server returned HTTP {response.status_code}.")

        session_id = response.headers.get(_SESSION_HEADER)
        if session_id:
            self._session_id = session_id

        message = _parse_response(response)
        if message is None:
            raise McpError("The MCP server sent no response to the request.")
        if "error" in message:
            error = message["error"]
            raise McpError(f"MCP server error: {error.get('message', 'unknown error')}")
        result: dict[str, Any] = message.get("result", {})
        return result

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        headers = {**self._headers, "Content-Type": "application/json", "Accept": _ACCEPT}
        if self._session_id is not None:
            headers[_SESSION_HEADER] = self._session_id
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        # A notification has no response to wait for; best-effort.
        with contextlib.suppress(httpx.HTTPError):
            await self._client.post(self._url, json=payload, headers=headers)


def _parse_response(response: httpx.Response) -> dict[str, Any] | None:
    """Extract the JSON-RPC message from a plain JSON or an SSE-framed response."""
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        parsed: Any = response.json()
        return parsed if isinstance(parsed, dict) else None

    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data:
                continue
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                _log.warning("non-JSON SSE data line from MCP server, ignored")
                continue
            if isinstance(parsed, dict) and "id" in parsed:
                return parsed
        return None

    try:
        parsed = json.loads(response.text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
