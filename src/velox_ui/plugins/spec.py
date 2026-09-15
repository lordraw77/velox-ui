"""The plugin contract (ADR-0014).

A plugin is discovered by entry-point metadata alone and imported only at first use,
and only if enabled — the same "ask what it can do, not what it is" discipline the
provider registry applies (``providers/registry.py``). ``images`` and ``voice`` are
the two groups phase 10 introduces; more groups can be added the same way later.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

import msgspec

from velox_ui.security.crypto import SecretBox

__all__ = [
    "AudioResult",
    "GeneratedImage",
    "ImagePlugin",
    "Plugin",
    "PluginConfig",
    "PluginInfo",
    "PluginKind",
    "PluginValidation",
    "VoicePlugin",
]

type PluginKind = Literal["images", "voice"]


class PluginConfig(msgspec.Struct, frozen=True):
    """Stored configuration for one plugin kind.

    Attributes:
        enabled: Whether the plugin may be loaded at all.
        base_url: The OpenAI-compatible host to call (ADR-0021).
        model: Backend model name, when the host needs one selected explicitly.
        extra: Small additional fields a specific plugin needs (e.g. TTS voice).
        api_key: The decrypted credential, or ``None`` if none is stored. Never
            logged and never echoed back by the API (``security/crypto.mask_secret``
            renders a hint for display instead).
    """

    enabled: bool
    base_url: str | None = None
    model: str | None = None
    extra: dict[str, str] = {}
    api_key: str | None = None


class PluginInfo(msgspec.Struct, frozen=True):
    """What ``GET /api/plugins`` reports for one discovered entry point."""

    name: str
    kind: PluginKind
    enabled: bool
    configured: bool
    description: str


class PluginValidation(msgspec.Struct, frozen=True):
    """The result of a plugin's ``validate()`` call."""

    ok: bool
    detail: str


class GeneratedImage(msgspec.Struct, frozen=True):
    """One generated image, transported as base64 inside the JSON envelope."""

    b64: str
    content_type: str


class AudioResult(msgspec.Struct, frozen=True):
    """Synthesized speech, as raw bytes plus the content type to serve it with."""

    data: bytes
    content_type: str


@runtime_checkable
class Plugin(Protocol):
    """What every plugin module exposes at its entry-point target."""

    name: str

    def __init__(self, config: PluginConfig, *, secrets: SecretBox) -> None:
        """Build the plugin from its stored configuration."""
        ...

    async def validate(self) -> PluginValidation:
        """Cheaply check the configured backend is reachable, without raising."""
        ...


@runtime_checkable
class ImagePlugin(Plugin, Protocol):
    """A plugin that can generate images."""

    async def generate(
        self, *, prompt: str, size: str | None = None, n: int = 1
    ) -> list[GeneratedImage]:
        """Generate one or more images from a prompt."""
        ...


@runtime_checkable
class VoicePlugin(Plugin, Protocol):
    """A plugin that can transcribe and synthesize speech."""

    async def transcribe(
        self, *, audio: bytes, content_type: str, language: str | None = None
    ) -> str:
        """Transcribe an audio clip to text."""
        ...

    async def speak(self, *, text: str, voice: str | None = None) -> AudioResult:
        """Synthesize speech for a piece of text."""
        ...


def config_from_dict(data: dict[str, Any]) -> PluginConfig:
    """Rebuild a :class:`PluginConfig` from a JSON-decoded ``Setting`` row.

    ``api_key`` is not part of the stored dict — it is decrypted separately from the
    ``secret`` table and passed in by the caller — so it defaults to ``None`` here.
    """
    return PluginConfig(
        enabled=bool(data.get("enabled", False)),
        base_url=data.get("base_url"),
        model=data.get("model"),
        extra=dict(data.get("extra") or {}),
    )
