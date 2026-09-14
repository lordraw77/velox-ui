"""The MCP client contract.

MCP (Model Context Protocol) is JSON-RPC 2.0 over one of a small set of transports.
velox-ui only needs the two operations a chat turn actually uses — discovering a
server's tools and calling one — plus the ``initialize`` handshake every transport
requires before either works, so this is not a spec-complete SDK: no resources,
prompts, sampling or roots support, none of which the product surface touches.

Both transports (:mod:`velox_ui.mcp.stdio`, :mod:`velox_ui.mcp.http_sse`) implement
:class:`McpClient` and share the JSON-RPC envelope helpers below.
"""

from __future__ import annotations

import itertools
from typing import Any, Protocol, runtime_checkable

import msgspec

from velox_ui.errors import ErrorCode, VeloxError

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "McpClient",
    "McpError",
    "McpTool",
    "McpToolResult",
    "next_request_id",
]

MCP_PROTOCOL_VERSION = "2025-06-18"

_id_counter = itertools.count(1)


def next_request_id() -> int:
    """A fresh JSON-RPC request id, unique within this process."""
    return next(_id_counter)


class McpError(VeloxError):
    """An MCP server rejected a request, or the transport failed."""

    code = ErrorCode.UPSTREAM_ERROR
    status_code = 502


class McpTool(msgspec.Struct, frozen=True):
    """One tool as an MCP server describes it.

    ``input_schema`` is JSON Schema, exactly as the server sent it — translation into
    velox-ui's internal :class:`~velox_ui.providers.base.ToolSpec` happens in
    :mod:`velox_ui.mcp.schema_translate`, not here, so this struct is a faithful
    record of the wire response (useful for the raw ``GET .../tools`` response too).
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = msgspec.field(default_factory=dict)


class McpToolResult(msgspec.Struct, frozen=True):
    """The outcome of a ``tools/call``.

    Attributes:
        content: Text content blocks, concatenated. MCP allows image/resource content
            blocks too; velox-ui's tool-result rendering is text-only for phase 8, so
            non-text blocks are summarized rather than dropped silently (see
            :func:`velox_ui.mcp.schema_translate.render_tool_result`).
        is_error: Whether the tool itself reported failure (a normal JSON-RPC success
            response can still carry ``isError: true`` — that is the tool failing, not
            the transport).
    """

    content: str
    is_error: bool = False


@runtime_checkable
class McpClient(Protocol):
    """One live connection to an MCP server.

    Implementations own their transport's lifecycle: constructing one does not
    connect: :meth:`initialize` performs the handshake, and :meth:`close` releases
    the transport (kills the subprocess, closes the HTTP client).
    """

    async def initialize(self) -> None:
        """Perform the MCP handshake. Must be called before any other method."""
        ...

    async def list_tools(self) -> list[McpTool]:
        """Return every tool the server currently offers."""
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Invoke one tool and return its result."""
        ...

    async def close(self) -> None:
        """Release the transport. Safe to call more than once."""
        ...
