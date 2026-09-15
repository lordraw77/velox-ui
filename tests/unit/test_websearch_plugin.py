"""SSRF guard and HTML text extraction for the builtin websearch plugin."""

from __future__ import annotations

import pytest

from velox_ui.plugins.builtin.websearch import _check_url_is_safe, extract_text
from velox_ui.plugins.errors import PluginUpstreamError


async def test_rejects_non_http_scheme() -> None:
    with pytest.raises(PluginUpstreamError, match="non-HTTP"):
        await _check_url_is_safe("file:///etc/passwd")


async def test_rejects_loopback() -> None:
    with pytest.raises(PluginUpstreamError, match="disallowed"):
        await _check_url_is_safe("http://127.0.0.1/")


async def test_rejects_localhost_hostname() -> None:
    with pytest.raises(PluginUpstreamError, match="disallowed"):
        await _check_url_is_safe("http://localhost/")


async def test_rejects_private_range() -> None:
    with pytest.raises(PluginUpstreamError, match="disallowed"):
        await _check_url_is_safe("http://10.0.0.5/")


async def test_rejects_link_local_metadata_address() -> None:
    with pytest.raises(PluginUpstreamError, match="disallowed"):
        await _check_url_is_safe("http://169.254.169.254/")


async def test_rejects_url_with_no_host() -> None:
    with pytest.raises(PluginUpstreamError, match="no host"):
        await _check_url_is_safe("http:///path")


async def test_allows_a_public_looking_address() -> None:
    # 93.184.216.34 was example.com's long-standing address; used here only as a
    # public, non-reserved IPv4 literal so no real DNS lookup is needed.
    await _check_url_is_safe("http://93.184.216.34/")


def test_extract_text_strips_script_and_style() -> None:
    html = """
    <html><head><style>body { color: red; }</style></head>
    <body>
      <script>alert('hi')</script>
      <nav>Home | About</nav>
      <h1>Title</h1>
      <p>Hello   world.</p>
    </body></html>
    """
    text = extract_text(html)
    assert "Title" in text
    assert "Hello" in text
    assert "world." in text
    assert "alert" not in text
    assert "color: red" not in text
    assert "Home" not in text


def test_extract_text_collapses_whitespace() -> None:
    html = "<p>a</p>\n\n<p>   b   </p>"
    assert extract_text(html) == "a b"
