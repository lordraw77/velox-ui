"""Server-sent events, read one at a time as they arrive.

Both HTTP transports receive JSON-RPC messages framed as SSE: Streamable HTTP in the
body of a POST response, the legacy HTTP+SSE transport on a long-lived GET. Either
stream may stay open indefinitely and carry comments between events — the Python MCP
SDK sends ``: ping`` every 15 seconds — so it must be parsed incrementally and never
read to the end. Reading it whole is what used to hang ``HttpSseMcpClient`` forever
against a legacy endpoint: each ping reset httpx's per-read timeout, and the body
never ended.

Only the parts of the SSE format MCP uses are kept: ``event`` and ``data``. ``id`` and
``retry`` exist for resuming a dropped stream, which velox-ui never does — every
connection lives for one operation (``mcp/manager.py``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

__all__ = ["SseEvent", "iter_sse_events", "parse_json_rpc"]


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One dispatched event.

    Attributes:
        event: The event type; ``"message"`` when the stream named none, as the SSE
            specification defines.
        data: Every ``data`` line of the event, joined with newlines.
    """

    event: str
    data: str


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Yield events from a stream of lines, as each one completes.

    Args:
        lines: Decoded lines without their terminators, e.g. ``response.aiter_lines()``.
    """
    event_name = ""
    data_lines: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data_lines:
                yield SseEvent(event=event_name or "message", data="\n".join(data_lines))
            event_name, data_lines = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    # A stream that ends without the blank line that should close its last event.
    if data_lines:
        yield SseEvent(event=event_name or "message", data="\n".join(data_lines))


def parse_json_rpc(raw: str | bytes) -> dict[str, Any] | None:
    """Return one JSON-RPC message object, or ``None`` for anything else."""
    try:
        parsed: Any = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None
