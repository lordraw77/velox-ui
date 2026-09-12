"""First-run autodiscovery of local backends.

At first start, velox-ui probes the ports local inference servers conventionally
listen on and configures whatever answers. This is what makes ``docker run`` followed
by "it already found my Ollama" true, and it is the smallest version of that promise
that is defensible: it touches only the loopback interface and a handful of known
ports, never the LAN. Scanning a network is something a person asks for explicitly,
never something a chat client does on its own (ADR-0008).

Probes are cheap, concurrent, and short. Nothing here can fail startup: every host is
either identified or silently ignored.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

__all__ = ["WELL_KNOWN", "Discovered", "probe_local_backends"]

_log = logging.getLogger("velox.providers.discovery")

# (base URL, kind) pairs, in the order a local-first client should prefer them.
WELL_KNOWN: tuple[tuple[str, str], ...] = (
    ("http://127.0.0.1:11434", "ollama"),
    ("http://127.0.0.1:8080", "llamacpp"),
    ("http://127.0.0.1:1234", "openai_compat"),  # LM Studio
    ("http://127.0.0.1:8000", "openai_compat"),  # vLLM
)

_PROBE_TIMEOUT_S = 0.75


class Discovered:
    """A backend that answered a probe.

    Attributes:
        base_url: Where it answered.
        kind: Which adapter should drive it.
    """

    __slots__ = ("base_url", "kind")

    def __init__(self, base_url: str, kind: str) -> None:
        """Store the discovery result."""
        self.base_url = base_url
        self.kind = kind

    def __repr__(self) -> str:
        """Return a debug representation."""
        return f"Discovered(base_url={self.base_url!r}, kind={self.kind!r})"


async def _probe(client: httpx.AsyncClient, base_url: str, kind: str) -> Discovered | None:
    """Probe one candidate.

    The probe is the backend's own identifying endpoint rather than a bare TCP
    connect, so an unrelated service that happens to hold the port is not mistaken for
    an inference server.
    """
    path = {"ollama": "/api/tags", "llamacpp": "/props", "openai_compat": "/v1/models"}[kind]
    try:
        response = await client.get(
            f"{base_url}{path}",
            timeout=httpx.Timeout(_PROBE_TIMEOUT_S, connect=_PROBE_TIMEOUT_S),
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return Discovered(base_url, kind)


async def probe_local_backends(client: httpx.AsyncClient) -> list[Discovered]:
    """Probe every well-known local port concurrently.

    Args:
        client: The shared HTTP client.

    Returns:
        Whatever answered, in :data:`WELL_KNOWN` order. Never raises.
    """
    results = await asyncio.gather(
        *(_probe(client, base_url, kind) for base_url, kind in WELL_KNOWN),
        return_exceptions=True,
    )
    found = [item for item in results if isinstance(item, Discovered)]
    if found:
        _log.info(
            "discovered local inference backends",
            extra={"backends": [f"{item.kind} at {item.base_url}" for item in found]},
        )
    return found
