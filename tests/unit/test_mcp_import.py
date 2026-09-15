"""Claude Code MCP config parsing: stdio, http, wrapped and bare shapes."""

from __future__ import annotations

import pytest

from velox_ui.services.mcp_import import McpImportError, parse_claude_mcp_config


def test_wrapped_stdio_server() -> None:
    raw = b"""
    {
      "mcpServers": {
        "discogs": {
          "type": null,
          "command": "npx",
          "args": ["-y", "discogs-mcp-server"],
          "env": {"DISCOGS_PERSONAL_ACCESS_TOKEN": "secret"},
          "cwd": null,
          "url": null,
          "headers": {}
        }
      }
    }
    """
    imported = parse_claude_mcp_config(raw)
    assert len(imported) == 1
    server = imported[0]
    assert server.name == "discogs"
    assert server.transport == "stdio"
    assert server.config == {
        "command": "npx",
        "args": ["-y", "discogs-mcp-server"],
        "env": {"DISCOGS_PERSONAL_ACCESS_TOKEN": "secret"},
    }
    assert server.dropped_cwd is False


def test_http_server() -> None:
    raw = (
        b'{"mcpServers": {"remote": '
        b'{"url": "https://example.com/mcp", "headers": {"X-Api-Key": "k"}}}}'
    )
    imported = parse_claude_mcp_config(raw)
    assert imported[0].transport == "http_sse"
    assert imported[0].config == {
        "url": "https://example.com/mcp",
        "headers": {"X-Api-Key": "k"},
    }


def test_bare_name_keyed_map_without_wrapper() -> None:
    raw = b'{"discogs": {"command": "npx", "args": ["-y", "discogs-mcp-server"]}}'
    imported = parse_claude_mcp_config(raw)
    assert imported[0].name == "discogs"
    assert imported[0].config == {"command": "npx", "args": ["-y", "discogs-mcp-server"]}


def test_single_bare_entry_without_name() -> None:
    raw = b'{"command": "npx", "args": ["-y", "discogs-mcp-server"]}'
    imported = parse_claude_mcp_config(raw)
    assert imported[0].name == "imported"


def test_multiple_servers_preserve_order() -> None:
    raw = b"""
    {"mcpServers": {
      "a": {"command": "npx", "args": []},
      "b": {"url": "https://example.com/mcp"}
    }}
    """
    imported = parse_claude_mcp_config(raw)
    assert [s.name for s in imported] == ["a", "b"]


def test_cwd_is_dropped_but_reported() -> None:
    raw = b'{"command": "npx", "args": [], "cwd": "/srv/app"}'
    imported = parse_claude_mcp_config(raw)
    assert imported[0].dropped_cwd is True
    assert "cwd" not in imported[0].config


def test_invalid_json_raises() -> None:
    with pytest.raises(McpImportError, match="not valid JSON"):
        parse_claude_mcp_config(b"{not json")


def test_entry_without_command_or_url_raises() -> None:
    with pytest.raises(McpImportError, match="neither"):
        parse_claude_mcp_config(b'{"mcpServers": {"broken": {"env": {}}}}')


def test_empty_object_raises() -> None:
    with pytest.raises(McpImportError):
        parse_claude_mcp_config(b"{}")


def test_non_object_raises() -> None:
    with pytest.raises(McpImportError):
        parse_claude_mcp_config(b"[1, 2, 3]")
