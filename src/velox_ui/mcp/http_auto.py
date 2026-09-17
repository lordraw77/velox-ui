"""Choose the HTTP transport by asking the server.

For a URL whose transport is not known — a pasted address, or an imported config with
no ``type`` — this follows the MCP specification's own backwards-compatibility
procedure (2025-03-26, "Backwards Compatibility"): send the ``initialize`` request as
Streamable HTTP, and if that fails with a 4xx status, treat the server as an older
HTTP+SSE one, ``GET`` the same URL and expect an ``endpoint`` event.

One addition, found against a real server rather than in the specification: the
Python MCP SDK's legacy ``/sse`` route answers a ``POST`` with 200 and an open event
stream whose first event is ``endpoint``, not with a 4xx. That answer is taken as the
same signal.

Only those two signals switch transports. A timeout, a refused connection or a 401 is
a real failure of this server, and is reported as one rather than retried as the
other transport.
"""

from __future__ import annotations

from typing import Any

import httpx

from velox_ui.mcp.client import (
    McpClient,
    McpError,
    McpHttpStatusError,
    McpLegacySseEndpointError,
    McpTool,
    McpToolResult,
)
from velox_ui.mcp.http_sse import HttpSseMcpClient, default_http_client
from velox_ui.mcp.sse import SseMcpClient

__all__ = ["AutoHttpMcpClient"]

# What a server that does not implement Streamable HTTP answers a POST with.
_NOT_STREAMABLE_STATUSES = frozenset({400, 404, 405})


class AutoHttpMcpClient:
    """Streamable HTTP when the server speaks it, legacy HTTP+SSE when it does not.

    Args:
        url: The server's URL, whichever transport it turns out to be.
        headers: Extra headers, as for either HTTP transport.
        client: An ``httpx.AsyncClient`` shared by both attempts.
    """

    __slots__ = ("_client", "_delegate", "_headers", "_owns_client", "_url")

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store the URL; nothing is opened until :meth:`initialize`."""
        self._url = url
        self._headers = headers or {}
        self._owns_client = client is None
        self._client = client or default_http_client()
        self._delegate: McpClient | None = None

    @property
    def transport(self) -> str | None:
        """The transport detected by :meth:`initialize`: ``"http_sse"`` or ``"sse"``."""
        if isinstance(self._delegate, HttpSseMcpClient):
            return "http_sse"
        if isinstance(self._delegate, SseMcpClient):
            return "sse"
        return None

    async def initialize(self) -> None:
        """Try Streamable HTTP, fall back to HTTP+SSE on the signals above.

        Raises:
            McpError: The server answered neither transport, or failed for a reason
                that is not a transport mismatch.
        """
        streamable = HttpSseMcpClient(url=self._url, headers=self._headers, client=self._client)
        try:
            await streamable.initialize()
        except McpLegacySseEndpointError:
            reason = "it announced a legacy message endpoint"
        except McpHttpStatusError as exc:
            if exc.http_status not in _NOT_STREAMABLE_STATUSES:
                await streamable.close()
                raise
            reason = f"it answered HTTP {exc.http_status}"
        except BaseException:
            await streamable.close()
            raise
        else:
            self._delegate = streamable
            return
        await streamable.close()

        legacy = SseMcpClient(url=self._url, headers=self._headers, client=self._client)
        try:
            await legacy.initialize()
        except McpError as exc:
            await legacy.close()
            raise McpError(
                f"MCP server at {self._url} speaks neither HTTP transport. Streamable "
                f"HTTP: {reason}. HTTP+SSE: {exc.message}"
            ) from exc
        except BaseException:
            await legacy.close()
            raise
        self._delegate = legacy

    async def list_tools(self) -> list[McpTool]:
        """Return the server's tool list, over the detected transport."""
        return await self._connected().list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Call one tool, over the detected transport."""
        return await self._connected().call_tool(name, arguments)

    async def close(self) -> None:
        """Close the detected transport, then the shared client if this owns it."""
        delegate, self._delegate = self._delegate, None
        if delegate is not None:
            await delegate.close()
        if self._owns_client:
            await self._client.aclose()

    def _connected(self) -> McpClient:
        if self._delegate is None:
            raise McpError(f"MCP server at {self._url} is not connected.")
        return self._delegate
