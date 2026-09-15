"""The builtin voice plugin against a fake OpenAI-compatible server."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.fakes.media import create_app as create_media
from tests.fakes.server import FakeServer, run_fake
from velox_ui.plugins.builtin.voice import OpenAICompatVoicePlugin
from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import PluginConfig

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def media_server() -> Iterator[FakeServer]:
    """A fake media server that needs no credential."""
    with run_fake(create_media()) as server:
        yield server


def _plugin(server: FakeServer, *, api_key: str | None = None) -> OpenAICompatVoicePlugin:
    config = PluginConfig(enabled=True, base_url=server.base_url, api_key=api_key)
    return OpenAICompatVoicePlugin(config, secrets=object())  # type: ignore[arg-type]


async def test_transcribe_returns_text(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    text = await plugin.transcribe(audio=b"hello", content_type="audio/webm")
    assert text == "transcribed: hello"


async def test_transcribe_maps_5xx_to_upstream_error(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.transcribe(audio=b"fail", content_type="audio/webm")


async def test_transcribe_maps_malformed_body_to_upstream_error(
    media_server: FakeServer,
) -> None:
    plugin = _plugin(media_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.transcribe(audio=b"broken", content_type="audio/webm")


async def test_speak_returns_audio(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    result = await plugin.speak(text="hello there")
    assert result.data == b"fake-mp3-bytes"
    assert result.content_type.startswith("audio/")


async def test_speak_maps_5xx_to_upstream_error(media_server: FakeServer) -> None:
    plugin = _plugin(media_server)
    with pytest.raises(PluginUpstreamError):
        await plugin.speak(text="fail")


async def test_api_key_is_sent_as_bearer_token() -> None:
    with run_fake(create_media(api_key="sk-test-1234")) as server:
        plugin = _plugin(server, api_key="sk-test-1234")
        text = await plugin.transcribe(audio=b"hi", content_type="audio/webm")
        assert text == "transcribed: hi"

        unauthenticated = _plugin(server)
        with pytest.raises(PluginUpstreamError):
            await unauthenticated.transcribe(audio=b"hi", content_type="audio/webm")
