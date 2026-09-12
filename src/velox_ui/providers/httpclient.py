"""The shared HTTP client.

One ``httpx.AsyncClient`` per process, reused by every adapter, with HTTP/2 and a
connection pool sized for long-lived streaming requests and long keep-alives toward
local inference backends (docs/design/00-overview.md, HTTP client requirement).
Opening a fresh connection per completion would add a TCP and TLS handshake to every
turn — exactly the kind of cost the 15 ms TTFT budget has no room for.
"""

from __future__ import annotations

import httpx

__all__ = ["build_http_client"]


def build_http_client() -> httpx.AsyncClient:
    """Construct the process-wide HTTP client.

    Returns:
        An ``AsyncClient`` with HTTP/2 enabled and a pool tuned for a handful of
        long-lived streaming connections to local and cloud inference backends rather
        than many short-lived ones.
    """
    return httpx.AsyncClient(
        http2=True,
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=50,
            keepalive_expiry=300.0,
        ),
        # No default timeout: each adapter applies its own Timeouts (connect,
        # first-token, between-tokens) per request, because a sensible timeout for a
        # cloud call is a hang for a local model still loading from disk (ADR-0008).
        timeout=httpx.Timeout(None),
    )
