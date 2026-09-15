"""The builtin images plugin against a fake OpenAI-compatible server."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.fakes.media import create_app as create_media
from tests.fakes.server import FakeServer, run_fake
from velox_ui.plugins.builtin.images import OpenAICompatImagePlugin
from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import PluginConfig

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def media_server() -> Iterator[FakeServer]:
    """A fake media server that needs no credential."""
    with run_fake(create_media()) as server:
        yield server


def _plugin(server: FakeServer, *, api_key: str | None = None) -> OpenAICompatImagePlugin:
    config = PluginConfig(enabled=True, base_url=server.base_url, api_key=api_key)
    return OpenAICompatImagePlugin(config, secrets=object())  # type: ignore[arg-type]


async def test_generate_returns_images(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    images = await plugin.generate(prompt="a cat", n=2)
    assert len(images) == 2
    assert all(image.content_type == "image/png" for image in images)


async def test_generate_maps_5xx_to_upstream_error(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.generate(prompt="fail")


async def test_generate_maps_malformed_body_to_upstream_error(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.generate(prompt="broken")


async def test_validate_reports_ok_when_reachable(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    result = await plugin.validate()
    assert result.ok is True


async def test_validate_reports_not_ok_without_base_url() -> None:
    plugin = OpenAICompatImagePlugin(
        PluginConfig(enabled=True, base_url=None), secrets=object()  # type: ignore[arg-type]
    )
    result = await plugin.validate()
    assert result.ok is False
