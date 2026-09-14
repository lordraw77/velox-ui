"""MCP tool schema <-> velox-ui's internal ``ToolSpec``.

MCP already describes a tool with exactly the three fields ``ToolSpec`` has (name,
description, JSON Schema parameters), so this translation is close to the identity —
the only real work is defensive: MCP servers are untrusted external processes, and a
malformed ``inputSchema`` (missing, not an object, wrong ``type``) must not propagate
into a provider request that a cloud API would then reject as a 400.
"""

from __future__ import annotations

from typing import Any

from velox_ui.mcp.client import McpTool, McpToolResult
from velox_ui.providers.base import ToolSpec

__all__ = ["render_tool_result", "to_tool_spec", "to_tool_specs"]

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def to_tool_spec(tool: McpTool, *, server_name: str) -> ToolSpec:
    """Adapt one MCP tool into a :class:`ToolSpec`.

    Args:
        tool: The tool as the server described it.
        server_name: The owning server's name, folded into the tool's wire name
            (``"<server>__<tool>"``) so tools from two servers never collide once
            they are merged into one list for a provider request.

    Returns:
        A :class:`ToolSpec` with a JSON-Schema-object ``parameters``, defaulting to an
        empty object when the server did not send a usable schema.
    """
    schema = tool.input_schema
    if not isinstance(schema, dict) or schema.get("type") != "object":
        schema = _EMPTY_OBJECT_SCHEMA
    return ToolSpec(
        name=f"{server_name}__{tool.name}",
        description=tool.description or f"Tool '{tool.name}' from MCP server '{server_name}'.",
        parameters=schema,
    )


def to_tool_specs(tools: list[McpTool], *, server_name: str) -> list[ToolSpec]:
    """Adapt a server's whole tool list."""
    return [to_tool_spec(tool, server_name=server_name) for tool in tools]


def split_qualified_name(qualified_name: str) -> tuple[str, str] | None:
    """Split a ``"<server>__<tool>"`` wire name back into its parts.

    Returns ``None`` when the name does not carry the ``__`` separator this module
    introduces in :func:`to_tool_spec`, which means it names a non-MCP (builtin) tool.
    """
    if "__" not in qualified_name:
        return None
    server_name, _, tool_name = qualified_name.partition("__")
    return server_name, tool_name


def render_tool_result(result: McpToolResult) -> str:
    """Render a tool result as the text fed back to the model.

    MCP results can carry image or resource content blocks; :class:`McpToolResult`
    already reduces those to their text blocks (``mcp/client.py``), so a tool that
    returned only non-text content arrives here empty. That is summarized rather than
    silently dropped, since an empty ``tool`` message reads to a model as "no answer".
    """
    if result.content:
        return result.content
    return "[This tool returned no text content.]"
