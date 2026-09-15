"""Import MCP server definitions from Claude Code's own config shape.

Claude Code (and Claude Desktop, Cursor, and most other MCP hosts) describe a server
as one JSON object — optionally under a `mcpServers` map keyed by name — with
`command`/`args`/`env`/`cwd` for a stdio server or `url`/`headers` for an HTTP one.
velox-ui's own MCP config (`db/models.py::McpServer.config`) is close enough to be a
direct field-by-field translation; this reads their shape, the same "read the export,
not the internals" approach phase 9's Open WebUI importer takes.

Credentials found in `env`/`headers` are carried over as plain config, exactly as
`services/mcp_import` received them: `manager.py::build_client` already treats
`config.env`/`config.headers` as non-secret (the encrypted path is the separate
`auth_token` field, injected at connect time). Promoting one particular env var to
that encrypted field would require guessing which key is a secret, so this importer
does not attempt it — a user who wants a credential encrypted moves it there after
import, from the server's own settings.
"""

from __future__ import annotations

import json
from typing import Any

import msgspec

__all__ = ["ImportedMcpServer", "McpImportError", "parse_claude_mcp_config"]


class McpImportError(ValueError):
    """The input is not a Claude Code MCP config this importer understands."""


class ImportedMcpServer(msgspec.Struct, frozen=True):
    """One server, translated into velox-ui's create-server shape.

    Attributes:
        name: The key it was found under (or ``"imported"`` for a single bare entry).
        transport: ``"stdio"`` or ``"http_sse"``.
        config: Ready to pass as ``CreateMcpServerRequest.config``.
        dropped_cwd: Whether the source entry named a working directory, which
            velox-ui's stdio transport has no field for and so does not carry over.
    """

    name: str
    transport: str
    config: dict[str, Any]
    dropped_cwd: bool = False


def parse_claude_mcp_config(raw: bytes | str) -> list[ImportedMcpServer]:
    """Parse a Claude Code ``mcpServers`` map, a bare name-keyed map, or one entry.

    Args:
        raw: The file's or pasted snippet's bytes or text.

    Returns:
        One :class:`ImportedMcpServer` per server found, in the source's order.

    Raises:
        McpImportError: If the input isn't valid JSON, or names no server this
            importer recognises (an entry needs a ``command`` or a ``url``).
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpImportError(f"not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or not data:
        raise McpImportError(
            "expected a Claude Code 'mcpServers' map, a name-keyed map of servers, "
            "or a single server object"
        )

    if isinstance(data.get("mcpServers"), dict):
        entries = data["mcpServers"]
    elif _looks_like_entry(data):
        entries = {"imported": data}
    elif all(isinstance(value, dict) for value in data.values()):
        entries = data
    else:
        raise McpImportError(
            "expected a Claude Code 'mcpServers' map, a name-keyed map of servers, "
            "or a single server object"
        )

    if not entries:
        raise McpImportError("no servers found")
    return [_translate(name, entry) for name, entry in entries.items()]


def _looks_like_entry(data: dict[str, Any]) -> bool:
    return "command" in data or "url" in data


def _translate(name: str, entry: Any) -> ImportedMcpServer:
    if not isinstance(entry, dict):
        raise McpImportError(f"{name!r}: server entry must be an object")

    url = entry.get("url")
    if url:
        config: dict[str, Any] = {"url": url}
        headers = entry.get("headers")
        if headers:
            config["headers"] = dict(headers)
        return ImportedMcpServer(name=name, transport="http_sse", config=config)

    command = entry.get("command")
    if command:
        config = {"command": command, "args": list(entry.get("args") or [])}
        env = entry.get("env")
        if env:
            config["env"] = dict(env)
        return ImportedMcpServer(
            name=name,
            transport="stdio",
            config=config,
            dropped_cwd=bool(entry.get("cwd")),
        )

    raise McpImportError(f"{name!r}: entry has neither 'command' nor 'url'")
