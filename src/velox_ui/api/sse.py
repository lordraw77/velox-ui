r"""Server-sent event framing.

This is the hot path, so the framing is byte-level and allocation-light (ADR-0004).
Two things follow from that:

* **Event frames are assembled from cached byte literals**, not formatted strings. A
  token delta becomes ``b"event: delta\\ndata: {\\"t\\":"`` + the JSON-escaped text +
  ``b"}\\n\\n"``: one escape pass, no intermediate dict, no second JSON encode.
* **Heartbeats are comments, not events.** A backend generating at one token per
  second is a supported configuration, and a reverse proxy that sees no bytes for
  thirty seconds will close the connection. A ``:`` comment line keeps the connection
  alive without the client having to know it exists.
"""

from __future__ import annotations

from typing import Any, Final

import msgspec

__all__ = [
    "HEARTBEAT",
    "SSE_HEADERS",
    "encode_event",
    "encode_text_delta",
]

_encoder: Final = msgspec.json.Encoder()

# Pre-built frame fragments for the only event emitted per token.
_DELTA_HEAD: Final = b'event: delta\ndata: {"t":'
_FRAME_TAIL: Final = b"}\n\n"

HEARTBEAT: Final = b": ping\n\n"
"""A comment frame. Carries no event, keeps proxies from closing an idle stream."""

SSE_HEADERS: Final = {
    "Cache-Control": "no-store",
    "Connection": "keep-alive",
    # Tells nginx not to buffer the response. Without it a proxy will happily
    # accumulate an entire generation and deliver it in one piece, which turns
    # streaming into a slow non-streaming request.
    "X-Accel-Buffering": "no",
}


def encode_text_delta(text: str) -> bytes:
    """Frame one token of assistant text.

    The single hottest function in the codebase: it runs once per token, at up to
    several hundred tokens per second per stream.

    Args:
        text: The chunk of text as the backend produced it.

    Returns:
        A complete SSE frame.
    """
    return _DELTA_HEAD + _encoder.encode(text) + _FRAME_TAIL


def encode_event(event: str, payload: Any) -> bytes:
    """Frame any non-delta event.

    Args:
        event: The SSE event name, from the closed set in docs/design/03-http-api.md.
        payload: A JSON-serialisable body.

    Returns:
        A complete SSE frame.
    """
    return b"event: " + event.encode("ascii") + b"\ndata: " + _encoder.encode(payload) + b"\n\n"
