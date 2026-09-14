"""A tiny, real MCP server speaking JSON-RPC over stdio, for offline tests.

Run as ``python -m tests.fakes.mcp_stdio_server``. Implements exactly the handshake
and the two operations velox-ui's :mod:`velox_ui.mcp.stdio` client exercises:
``initialize``, ``tools/list`` (one tool, ``get_weather``) and ``tools/call`` (returns
a deterministic text result derived from the ``city`` argument, or an error result for
an unknown tool name — enough to test both the happy path and a tool reporting failure
without a second fixture).
"""

from __future__ import annotations

import json
import sys


def _write(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    """Read newline-delimited JSON-RPC requests from stdin until EOF."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-mcp", "version": "1"},
                    },
                }
            )
        elif method == "notifications/initialized":
            continue  # a notification carries no id and expects no response
        elif method == "tools/list":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "get_weather",
                                "description": "Look up the current weather for a city.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                    "required": ["city"],
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = request.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "get_weather":
                city = arguments.get("city", "an unknown city")
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": f"It is sunny in {city}."}],
                            "isError": False,
                        },
                    }
                )
            else:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Unknown tool {name}"}],
                            "isError": True,
                        },
                    }
                )
        else:
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
