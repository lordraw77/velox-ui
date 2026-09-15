"""Plugin spec: config decoding and Protocol conformance."""

from __future__ import annotations

from velox_ui.plugins.spec import ImagePlugin, PluginConfig, VoicePlugin, config_from_dict


def test_config_from_dict_defaults() -> None:
    config = config_from_dict({})
    assert config == PluginConfig(enabled=False, base_url=None, model=None, extra={})


def test_config_from_dict_round_trips_fields() -> None:
    config = config_from_dict(
        {
            "enabled": True,
            "base_url": "http://localhost:9000",
            "model": "sdxl",
            "extra": {"a": "b"},
        }
    )
    assert config.enabled is True
    assert config.base_url == "http://localhost:9000"
    assert config.model == "sdxl"
    assert config.extra == {"a": "b"}


class _FakeImagePlugin:
    name = "fake-images"

    def __init__(self, config: PluginConfig, *, secrets: object) -> None:
        del config, secrets

    async def validate(self) -> object:
        raise NotImplementedError

    async def generate(
        self, *, prompt: str, size: str | None = None, n: int = 1
    ) -> list[object]:
        del prompt, size, n
        return []


class _FakeVoicePlugin:
    name = "fake-voice"

    def __init__(self, config: PluginConfig, *, secrets: object) -> None:
        del config, secrets

    async def validate(self) -> object:
        raise NotImplementedError

    async def transcribe(
        self, *, audio: bytes, content_type: str, language: str | None = None
    ) -> str:
        del audio, content_type, language
        return ""

    async def speak(self, *, text: str, voice: str | None = None) -> object:
        del text, voice
        raise NotImplementedError


def test_fake_plugins_satisfy_their_protocols() -> None:
    image_plugin = _FakeImagePlugin(PluginConfig(enabled=True), secrets=object())
    voice_plugin = _FakeVoicePlugin(PluginConfig(enabled=True), secrets=object())
    assert isinstance(image_plugin, ImagePlugin)
    assert isinstance(voice_plugin, VoicePlugin)
