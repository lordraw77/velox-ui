"""Builtin web-search and page-browsing tools, over a self-hosted SearXNG instance.

Registered under the ``velox_ui.tools`` entry-point group (ADR-0014) — the group the
project reserved from the start for exactly this: a builtin, non-MCP tool a model can
call mid-turn (``api/routes/tools.py``'s own docstring names it as the documented path).

Only SearXNG is supported (self-hosted, no API key, ADR-0008's local-first bias);
other search backends can be added the same way provider presets were, later.

``web_browse`` fetches a URL the *model* chose, on its own initiative, which is a real
SSRF surface for a self-hosted server: a model could be steered into asking this
process to fetch ``http://169.254.169.254/`` (a cloud metadata endpoint) or an address
on the deployment's own LAN. Every hostname is resolved and checked against
loopback/private/link-local/reserved/multicast ranges *before* connecting, and every
redirect hop is checked again before being followed — a public first hop redirecting
to a private address is refused just as if it had been requested directly.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import PluginConfig, PluginValidation, ToolDefinition
from velox_ui.security.crypto import SecretBox

__all__ = ["SearxngToolPlugin"]

_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
_DEFAULT_MAX_RESULTS = 5
_MAX_BODY_BYTES = 1_500_000
_MAX_TEXT_CHARS = 8_000
_MAX_REDIRECTS = 3
_SKIP_TAGS = frozenset({"script", "style", "noscript", "nav", "footer", "template"})


class _SsrfError(PluginUpstreamError):
    """A browse target resolved to, or redirected to, a disallowed address."""


class _TextExtractor(HTMLParser):
    """A minimal, dependency-free HTML-to-text extractor.

    Not a Readability-style content extractor — it keeps every visible text node
    outside ``<script>``/``<style>``/``<nav>``/``<footer>`` and collapses whitespace.
    Good enough for a model to read a page's gist; exact fidelity is not the goal.
    """

    def __init__(self) -> None:
        """Start with no skipped-tag depth and an empty buffer."""
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Enter a skipped region for noisy tags."""
        del attrs
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Leave a skipped region."""
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        """Collect visible text."""
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        """Return the collected text, whitespace-collapsed."""
        return " ".join(self._parts)


def extract_text(html: str) -> str:
    """Extract readable text from an HTML document, stdlib only."""
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.text()


async def _resolve_addresses(host: str) -> list[str]:
    """Resolve a hostname to its IP addresses, off the event loop (blocking DNS)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, host, None)
    except OSError as exc:
        raise _SsrfError(f"Could not resolve host {host!r}: {exc}") from exc
    return [str(info[4][0]) for info in infos]


def _is_disallowed(address: str) -> bool:
    """Whether an address must never be fetched by this process."""
    ip = ipaddress.ip_address(address)
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _check_url_is_safe(url: str) -> None:
    """Reject a URL whose scheme or resolved address is disallowed.

    Raises:
        PluginUpstreamError: If the scheme isn't http(s), or every/any resolved
            address for the host is loopback, private, link-local, reserved or
            multicast.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise _SsrfError(f"Refusing to fetch a non-HTTP(S) URL: {url!r}")
    if not parsed.hostname:
        raise _SsrfError(f"URL has no host: {url!r}")
    addresses = await _resolve_addresses(parsed.hostname)
    if not addresses or any(_is_disallowed(address) for address in addresses):
        raise _SsrfError(f"Refusing to fetch {url!r}: resolves to a disallowed address")


class SearxngToolPlugin:
    """Web search and page browsing over a self-hosted SearXNG instance."""

    name = "searxng-websearch"

    def __init__(self, config: PluginConfig, *, secrets: SecretBox) -> None:
        """Store the configuration; every call opens its own short-lived client."""
        del secrets  # SearXNG needs no credential; api_key is unused here
        self._config = config

    def tools(self) -> list[ToolDefinition]:
        """Describe ``web_search`` and ``web_browse``."""
        return [
            ToolDefinition(
                name="web_search",
                description="Search the web for current information.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."},
                        "n": {
                            "type": "integer",
                            "description": "Maximum number of results.",
                            "default": _DEFAULT_MAX_RESULTS,
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="web_browse",
                description="Fetch a web page and return its readable text content.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The page to fetch."},
                    },
                    "required": ["url"],
                },
            ),
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Dispatch to ``web_search`` or ``web_browse``.

        Raises:
            PluginUpstreamError: If the tool name is neither, the backend fails, or
                (for ``web_browse``) the URL is disallowed.
        """
        if name == "web_search":
            query = str(arguments.get("query", "")).strip()
            n = int(arguments.get("n") or _DEFAULT_MAX_RESULTS)
            return await self._search(query, n)
        if name == "web_browse":
            url = str(arguments.get("url", "")).strip()
            return await self._browse(url)
        raise PluginUpstreamError(f"Unknown tool {name!r}.")

    async def _search(self, query: str, n: int) -> str:
        base_url = (self._config.base_url or "").rstrip("/")
        max_results = min(n, int(self._config.extra.get("max_results", _DEFAULT_MAX_RESULTS)))
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{base_url}/search", params={"q": query, "format": "json"}
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise PluginUpstreamError(
                    f"Search backend at {base_url!r} did not answer: {exc}"
                ) from exc
        try:
            results = response.json()["results"][:max_results]
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginUpstreamError(
                f"Search backend at {base_url!r} returned an unexpected response."
            ) from exc
        if not results:
            return "No results found."
        lines = []
        for index, item in enumerate(results, start=1):
            title = item.get("title", "(untitled)")
            url = item.get("url", "")
            snippet = (item.get("content") or "").strip()
            lines.append(f"{index}. {title}\n   {url}\n   {snippet}")
        return "\n".join(lines)

    async def _browse(self, url: str) -> str:
        await _check_url_is_safe(url)
        current = url
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                try:
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise PluginUpstreamError(f"Redirect with no location: {url!r}")
                            current = str(httpx.URL(current).join(location))
                            await _check_url_is_safe(current)
                            continue
                        response.raise_for_status()
                        body = b""
                        async for chunk in response.aiter_bytes():
                            body += chunk
                            if len(body) >= _MAX_BODY_BYTES:
                                break
                        html = body.decode(response.encoding or "utf-8", errors="replace")
                        text = extract_text(html)
                        return text[:_MAX_TEXT_CHARS]
                except httpx.HTTPError as exc:
                    raise PluginUpstreamError(f"Could not fetch {url!r}: {exc}") from exc
            raise PluginUpstreamError(f"Too many redirects fetching {url!r}.")

    async def validate(self) -> PluginValidation:
        """Probe the configured SearXNG instance, tolerating any failure."""
        base_url = (self._config.base_url or "").rstrip("/")
        if not base_url:
            return PluginValidation(ok=False, detail="No base_url configured.")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(
                    f"{base_url}/search", params={"q": "test", "format": "json"}
                )
            if response.is_success:
                return PluginValidation(ok=True, detail="Backend reachable.")
            return PluginValidation(
                ok=False, detail=f"Backend responded with HTTP {response.status_code}."
            )
        except httpx.HTTPError as exc:
            return PluginValidation(ok=False, detail=f"Backend unreachable: {exc}")
