"""The builtin websearch plugin against a fake SearXNG instance."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator

import pytest

import velox_ui.plugins.builtin.websearch as websearch_module
from tests.fakes.searxng import create_app as create_searxng
from tests.fakes.server import FakeServer, run_fake
from velox_ui.plugins.builtin.websearch import SearxngToolPlugin
from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import PluginConfig

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def searxng_server() -> Iterator[FakeServer]:
    """A fake SearXNG instance on a real localhost port."""
    with run_fake(create_searxng()) as server:
        yield server


def _plugin(server: FakeServer) -> SearxngToolPlugin:
    return SearxngToolPlugin(
        PluginConfig(enabled=True, base_url=server.base_url),
        secrets=object(),  # type: ignore[arg-type]
    )


_REAL_CHECK = websearch_module._check_url_is_safe


async def _noop_check(url: str) -> None:
    del url


def _allow_first_hop_only(allowed_base_url: str) -> Callable[[str], Awaitable[None]]:
    async def _check(url: str) -> None:
        if not url.startswith(allowed_base_url):
            await _REAL_CHECK(url)

    return _check


async def test_tools_declares_search_and_browse(searxng_server: FakeServer) -> None:
    names = {tool.name for tool in _plugin(searxng_server).tools()}
    assert names == {"web_search", "web_browse"}


async def test_offers_nothing_without_a_configured_backend() -> None:
    """With no base_url there is nothing to search, so the model is handed no
    tool rather than one that could only fail — this is what lets the tools
    plugin kind be enabled just for its configuration-free tools."""
    plugin = SearxngToolPlugin(PluginConfig(enabled=True), secrets=object())  # type: ignore[arg-type]
    assert plugin.tools() == []


async def test_search_returns_formatted_results(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    result = await plugin.call("web_search", {"query": "turin weather"})
    assert "Result for turin weather" in result
    assert "https://example.invalid/a" in result


async def test_search_respects_n(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    result = await plugin.call("web_search", {"query": "x", "n": 1})
    assert "Second result" not in result


async def test_search_maps_5xx_to_upstream_error(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.call("web_search", {"query": "fail"})


async def test_search_maps_malformed_body_to_upstream_error(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.call("web_search", {"query": "broken"})


async def test_browse_extracts_page_text(
    searxng_server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake server itself is on loopback, which the SSRF guard (correctly)
    # refuses on its own — that guard is exercised end-to-end below and directly
    # in tests/unit/test_websearch_plugin.py. Here it's disabled so this test can
    # isolate the fetch/extract behaviour against a same-host fake.
    monkeypatch.setattr(websearch_module, "_check_url_is_safe", _noop_check)
    plugin = _plugin(searxng_server)
    result = await plugin.call("web_browse", {"url": f"{searxng_server.base_url}/pages/a"})
    assert "A Page" in result
    assert "Hello from a fake page." in result
    assert "evil()" not in result


async def test_browse_follows_a_safe_redirect(
    searxng_server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(websearch_module, "_check_url_is_safe", _noop_check)
    plugin = _plugin(searxng_server)
    result = await plugin.call(
        "web_browse", {"url": f"{searxng_server.base_url}/pages/redirect"}
    )
    assert "Hello from a fake page." in result


async def test_browse_refuses_a_redirect_to_a_private_address(
    searxng_server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The *first* hop is allowed (patched, as above); the guard must still catch
    # the private address the redirect points at, proving each hop is re-checked.
    monkeypatch.setattr(
        websearch_module, "_check_url_is_safe", _allow_first_hop_only(searxng_server.base_url)
    )
    plugin = _plugin(searxng_server)
    with pytest.raises(PluginUpstreamError, match="disallowed"):
        await plugin.call(
            "web_browse", {"url": f"{searxng_server.base_url}/pages/redirect-to-private"}
        )


async def test_browse_refuses_a_direct_private_url(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    with pytest.raises(PluginUpstreamError, match="disallowed"):
        await plugin.call("web_browse", {"url": "http://127.0.0.1/"})


async def test_unknown_tool_name_raises(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    with pytest.raises(PluginUpstreamError, match="Unknown tool"):
        await plugin.call("something_else", {})


async def test_validate_reports_ok_when_reachable(searxng_server: FakeServer) -> None:
    plugin = _plugin(searxng_server)
    result = await plugin.validate()
    assert result.ok is True
