"""MCP over the legacy HTTP+SSE transport (protocol revision 2024-11-05).

Superseded by Streamable HTTP in the 2025-03-26 revision, but still what many
deployed servers speak — the Python MCP SDK's ``/sse`` route among them. The shape:

1. ``GET <url>`` opens an event stream. Its first event is ``endpoint``, whose data is
   the URL (usually relative, carrying a session id) that messages must be posted to.
2. Each JSON-RPC request is ``POST``ed there. The server acknowledges with 202, and the
   response arrives later as a ``message`` event on the stream from step 1.

So a response is not tied to the request that caused it, only to its JSON-RPC id. A
background task reads the stream for the client's whole life and resolves the pending
request with the matching id — the same arrangement ``mcp/stdio.py`` uses for a
subprocess's stdout. Every velox-ui connection lives for one operation
(``mcp/manager.py``), so there is no reconnecting and no resuming a dropped stream:
if the stream ends, every pending request fails at once instead of waiting out its
deadline.

The endpoint is announced by the server, which makes it an input to validate, not a
destination to trust. It must share the stream URL's origin: otherwise a server could
name another host, and the requests — with any ``Authorization`` header configured
for this server — would be sent there.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from velox_ui.mcp.client import (
    MCP_PROTOCOL_VERSION,
    McpError,
    McpHttpStatusError,
    McpTool,
    McpToolResult,
    next_request_id,
)
from velox_ui.mcp.http_sse import default_http_client
from velox_ui.mcp.sse_events import iter_sse_events, parse_json_rpc

__all__ = ["SseMcpClient"]

_log = logging.getLogger("velox.mcp.sse")

# The same budget the other transports give one call.
_REQUEST_TIMEOUT_S = 60.0
# A server that has not said where to post within this long is not going to.
_ENDPOINT_TIMEOUT_S = 10.0
_COURTESY_TIMEOUT_S = 5.0

_DEFAULT_PORTS = {"http": 80, "https": 443}


class SseMcpClient:
    """An MCP client transport for servers that speak the legacy HTTP+SSE transport.

    Args:
        url: The event-stream URL, typically ending in ``/sse``.
        headers: Extra headers sent on the stream and on every posted message,
            typically including a decrypted bearer token.
        client: An ``httpx.AsyncClient`` to use, as for
            :class:`~velox_ui.mcp.http_sse.HttpSseMcpClient`.
        timeout: Overall deadline for one request, response included.
        endpoint_timeout: How long to wait for the ``endpoint`` event.
    """

    __slots__ = (
        "_client",
        "_endpoint",
        "_endpoint_timeout",
        "_failure",
        "_failure_status",
        "_headers",
        "_owns_client",
        "_pending",
        "_reader",
        "_ready",
        "_timeout",
        "_url",
    )

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _REQUEST_TIMEOUT_S,
        endpoint_timeout: float = _ENDPOINT_TIMEOUT_S,
    ) -> None:
        """Store the stream URL; nothing is opened until :meth:`initialize`."""
        self._url = url
        self._headers = headers or {}
        self._owns_client = client is None
        self._client = client or default_http_client()
        self._timeout = timeout
        self._endpoint_timeout = endpoint_timeout
        self._endpoint: str | None = None
        self._failure: str | None = None
        self._failure_status: int | None = None
        self._ready = asyncio.Event()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Open the stream, wait for the message endpoint, and perform the handshake.

        Raises:
            McpHttpStatusError: The stream URL answered with an error status.
            McpError: No endpoint was announced in time, the one announced was refused,
                or the handshake failed.
        """
        self._reader = asyncio.create_task(self._read_stream())
        try:
            async with asyncio.timeout(self._endpoint_timeout):
                await self._ready.wait()
        except TimeoutError as exc:
            raise McpError(
                f"MCP server at {self._url} did not announce a message endpoint within "
                f"{self._endpoint_timeout:g} s; is it an HTTP+SSE MCP endpoint?"
            ) from exc
        if self._endpoint is None:
            raise self._failure_error()

        await self._request(
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
        result = await self._request("tools/list", {})
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
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        text_parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return McpToolResult(
            content="\n".join(text_parts), is_error=bool(result.get("isError", False))
        )

    async def close(self) -> None:
        """Close the stream, fail anything still waiting, release the client."""
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        self._fail("the MCP client was closed")
        if self._owns_client:
            await self._client.aclose()

    # -- the stream --------------------------------------------------------------------

    async def _read_stream(self) -> None:
        headers = {**self._headers, "Accept": "text/event-stream"}
        try:
            async with self._client.stream("GET", self._url, headers=headers) as response:
                if response.status_code >= 400:
                    self._fail(
                        f"MCP server at {self._url} returned HTTP {response.status_code}.",
                        http_status=response.status_code,
                    )
                    return
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    self._fail(
                        f"{self._url} did not open an event stream (Content-Type "
                        f"{content_type or 'missing'}); is it an HTTP+SSE MCP endpoint?"
                    )
                    return
                async for event in iter_sse_events(response.aiter_lines()):
                    if event.event == "endpoint":
                        if not self._accept_endpoint(event.data):
                            return
                        continue
                    message = parse_json_rpc(event.data)
                    if message is None:
                        _log.warning("non-JSON SSE event from MCP server, ignored")
                        continue
                    self._resolve(message)
            self._fail(f"MCP server at {self._url} closed its event stream.")
        except httpx.HTTPError as exc:
            self._fail(f"Could not reach the MCP server at {self._url}: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a reader that dies silently would strand every request
            _log.exception("MCP event stream reader failed")
            self._fail(f"Reading the MCP event stream from {self._url} failed: {exc}")

    def _accept_endpoint(self, data: str) -> bool:
        if self._endpoint is not None:
            return True  # announced once per stream; a repeat changes nothing
        candidate = urljoin(self._url, data.strip())
        if _origin(candidate) != _origin(self._url):
            self._fail(
                f"MCP server at {self._url} announced a message endpoint on another "
                f"origin ({_origin(candidate)}). Refused: no request, and no credential, "
                "is sent there."
            )
            return False
        self._endpoint = candidate
        self._ready.set()
        return True

    def _resolve(self, message: dict[str, Any]) -> None:
        if "result" not in message and "error" not in message:
            return  # a notification or a server-to-client request
        future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
        if future is not None and not future.done():
            future.set_result(message)

    def _fail(self, reason: str, *, http_status: int | None = None) -> None:
        if self._failure is None:
            self._failure = reason
            self._failure_status = http_status
        self._ready.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(self._failure_error())
        self._pending.clear()

    def _failure_error(self) -> McpError:
        reason = self._failure or f"MCP server at {self._url} is not connected."
        if self._failure_status is not None:
            return McpHttpStatusError(reason, http_status=self._failure_status)
        return McpError(reason)

    # -- messages ----------------------------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._endpoint is None or self._failure is not None:
            raise self._failure_error()
        request_id = next_request_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with asyncio.timeout(self._timeout):
                await self._send(payload, request_id=request_id, future=future)
                message = await future
        except TimeoutError as exc:
            raise McpError(
                f"MCP server at {self._url} did not answer {method!r} "
                f"within {self._timeout:g} s."
            ) from exc
        finally:
            self._pending.pop(request_id, None)

        if "error" in message:
            error = message["error"]
            raise McpError(f"MCP server error: {error.get('message', 'unknown error')}")
        result: dict[str, Any] = message.get("result", {})
        return result

    async def _send(
        self,
        payload: dict[str, Any],
        *,
        request_id: int | None = None,
        future: asyncio.Future[dict[str, Any]] | None = None,
    ) -> None:
        endpoint = self._endpoint
        if endpoint is None:
            raise self._failure_error()
        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            async with self._client.stream(
                "POST", endpoint, json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    raise McpHttpStatusError(
                        f"MCP server at {endpoint} returned HTTP {response.status_code}.",
                        http_status=response.status_code,
                    )
                # The transport answers 202 and delivers the response on the stream.
                # Some servers answer in the POST body instead; accept that as well.
                if future is not None and "application/json" in response.headers.get(
                    "content-type", ""
                ):
                    inline = parse_json_rpc(await response.aread())
                    if (
                        inline is not None
                        and inline.get("id") == request_id
                        and not future.done()
                    ):
                        future.set_result(inline)
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach the MCP server at {endpoint}: {exc}") from exc

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        with contextlib.suppress(McpError, TimeoutError):
            async with asyncio.timeout(_COURTESY_TIMEOUT_S):
                await self._send({"jsonrpc": "2.0", "method": method, "params": params})


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = httpx.URL(url)
    return (parsed.scheme, parsed.host, parsed.port or _DEFAULT_PORTS.get(parsed.scheme))
