"""Finding backends: first-run autodiscovery and probing an address.

At first start, velox-ui probes the ports local inference servers conventionally
listen on and configures whatever answers. This is what makes ``docker run`` followed
by "it already found my Ollama" true, and it is the smallest version of that promise
that is defensible: it touches only the loopback interface and the handful of ports
the preset catalogue lists, never the LAN. Scanning a network is something a person
asks for explicitly, never something a chat client does on its own (ADR-0008).

The candidates come from ``presets.toml`` (``discovery_ports``), so a preset added
there is discovered without code here.

:func:`probe_address` is the other half: a person typed an address and wants to know
what is there before saving it.

Probes are cheap, concurrent, and short. Nothing here raises: every host is either
identified or reported as not answering.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from velox_ui.providers.presets import Preset, load_presets

__all__ = ["Discovered", "ProbeResult", "candidates", "probe_address", "probe_local_backends"]

_log = logging.getLogger("velox.providers.discovery")

_PROBE_TIMEOUT_S = 0.75


@dataclass(frozen=True)
class Discovered:
    """A backend that answered a probe.

    Attributes:
        base_url: Where it answered, in the form its adapter expects.
        kind: Which adapter should drive it.
        preset: The preset it matched.
    """

    base_url: str
    kind: str
    preset: str


@dataclass(frozen=True)
class ProbeResult:
    """What, if anything, answered at an address.

    Attributes:
        reachable: Whether an inference API answered at all.
        kind: The adapter that fits, when identified.
        preset: A suggested preset: ``ollama``, ``llamacpp`` or ``custom``.
        base_url: The address normalised for that adapter, e.g. with ``/v1`` added.
        models: How many models it listed, when it listed any.
        latency_ms: Round trip of the identifying request.
        detail: Why it is not usable, when it is not.
    """

    reachable: bool
    kind: str | None = None
    preset: str | None = None
    base_url: str | None = None
    models: int | None = None
    latency_ms: float | None = None
    detail: str | None = None


def candidates() -> list[tuple[str, Preset]]:
    """Loopback addresses to probe, in catalogue order.

    The preset's own path prefix is kept (``/v1`` for OpenAI-compatible servers), with
    the host swapped for ``127.0.0.1`` and the port for each discovery port.
    """
    found: list[tuple[str, Preset]] = []
    for preset in load_presets().values():
        prefix = urlsplit(preset.base_url).path.rstrip("/")
        for port in preset.discovery_ports:
            found.append((f"http://127.0.0.1:{port}{prefix}", preset))
    return found


async def _identify(
    client: httpx.AsyncClient, base_url: str, preset: Preset
) -> Discovered | None:
    """Probe one candidate on the backend's own identifying endpoint.

    A bare TCP connect would mistake any service holding the port — including another
    velox-ui, whose default port is llama.cpp's — for an inference server.
    """
    try:
        response = await client.get(
            f"{base_url}{preset.probe_path}",
            timeout=httpx.Timeout(_PROBE_TIMEOUT_S, connect=_PROBE_TIMEOUT_S),
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return Discovered(base_url=base_url, kind=preset.kind, preset=preset.key)


async def probe_local_backends(client: httpx.AsyncClient) -> list[Discovered]:
    """Probe every discovery candidate concurrently.

    Args:
        client: The shared HTTP client.

    Returns:
        Whatever answered, in catalogue order. When two presets share a port, the
        first that answers wins. Never raises.
    """
    listed = candidates()
    results = await asyncio.gather(
        *(_identify(client, base_url, preset) for base_url, preset in listed),
        return_exceptions=True,
    )
    found: list[Discovered] = []
    seen_ports: set[int | None] = set()
    for item in results:
        if not isinstance(item, Discovered):
            continue
        port = urlsplit(item.base_url).port
        if port in seen_ports:
            continue
        seen_ports.add(port)
        found.append(item)
    if found:
        _log.info(
            "discovered local inference backends",
            extra={"backends": [f"{item.preset} at {item.base_url}" for item in found]},
        )
    return found


async def probe_address(
    client: httpx.AsyncClient, base_url: str, *, api_key: str | None = None
) -> ProbeResult:
    """Work out what kind of backend answers at an address.

    Tries, in order, Ollama's ``/api/tags``, llama.cpp's ``/props`` and an
    OpenAI-compatible ``/models`` — with and without a ``/v1`` prefix, because people
    paste both forms. Native APIs are tried first: llama.cpp also speaks the OpenAI
    protocol, and its native adapter is the better fit.

    Args:
        client: The shared HTTP client.
        base_url: The address as typed.
        api_key: Credential to present, for servers that require one.

    Returns:
        The identification. Never raises.
    """
    root = base_url.strip().rstrip("/")
    if not root.startswith(("http://", "https://")):
        return ProbeResult(
            reachable=False, detail="The address must start with http:// or https://."
        )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    bare = root[:-3] if root.endswith("/v1") else root

    attempts: list[tuple[str, str, str, str]] = [
        ("ollama", "ollama", bare, "/api/tags"),
        ("llamacpp", "llamacpp", bare, "/props"),
        ("openai_compat", "custom", f"{bare}/v1", "/models"),
    ]
    if root != bare or "/v1" not in root:
        attempts.append(("openai_compat", "custom", root, "/models"))

    last_detail = "Nothing answered at this address."
    for kind, preset, candidate, path in attempts:
        started = time.perf_counter()
        try:
            response = await client.get(
                f"{candidate}{path}", headers=headers, timeout=httpx.Timeout(3.0, connect=3.0)
            )
        except httpx.ConnectError as exc:
            # Refused or unresolvable: every other path on this host fails the same way.
            return ProbeResult(reachable=False, detail=f"Cannot connect: {exc}")
        except httpx.HTTPError as exc:
            last_detail = f"No answer: {exc}"
            continue
        latency_ms = (time.perf_counter() - started) * 1_000.0
        if response.status_code in (401, 403):
            return ProbeResult(
                reachable=True,
                kind=kind,
                preset=preset,
                base_url=candidate,
                latency_ms=latency_ms,
                detail="The backend answered but requires an API key.",
            )
        if response.status_code >= 400:
            last_detail = f"{candidate}{path} answered HTTP {response.status_code}."
            continue
        try:
            body: Any = response.json()
        except ValueError:
            last_detail = f"{candidate}{path} did not answer with JSON."
            continue
        return ProbeResult(
            reachable=True,
            kind=kind,
            preset=preset,
            base_url=candidate,
            models=_count_models(kind, body),
            latency_ms=latency_ms,
        )
    return ProbeResult(reachable=False, detail=last_detail)


def _count_models(kind: str, body: Any) -> int | None:
    """Count the models in an identifying response, when it lists any."""
    if not isinstance(body, dict):
        return None
    if kind == "ollama":
        return len(body.get("models") or [])
    if kind == "openai_compat":
        return len(body.get("data") or [])
    return 1
