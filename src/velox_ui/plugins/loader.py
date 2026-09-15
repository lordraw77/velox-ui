"""Entry-point discovery and lazy loading of plugins (ADR-0014).

Discovery reads entry-point *metadata* only — no plugin module is imported until
:func:`load` is called, and that only happens for an enabled plugin a route actually
needs. This is the first real use of :mod:`importlib.metadata` in the codebase; the
provider registry and the RAG embedder registry both considered and rejected entry
points in favour of a plain in-process registry, because at the time neither had a
genuine third-party extension point. Images and voice do.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Literal, overload

from velox_ui.plugins.spec import (
    ImagePlugin,
    Plugin,
    PluginConfig,
    PluginInfo,
    PluginKind,
    VoicePlugin,
    config_from_dict,
)

if TYPE_CHECKING:
    from velox_ui.security.crypto import SecretBox
    from velox_ui.state import AppState

__all__ = ["EntryPointMeta", "PluginRegistry", "discover", "load"]

_log = logging.getLogger("velox.plugins")

_GROUP_PREFIX = "velox_ui."


@dataclass(frozen=True)
class EntryPointMeta:
    """One discovered entry point, before its module is ever imported."""

    name: str
    kind: PluginKind
    value: str


def discover(kind: PluginKind) -> list[EntryPointMeta]:
    """List every entry point registered under ``velox_ui.<kind>``.

    Reads package metadata only (``importlib.metadata.entry_points``); nothing named
    here is imported by this call.
    """
    group = f"{_GROUP_PREFIX}{kind}"
    return [
        EntryPointMeta(name=entry_point.name, kind=kind, value=entry_point.value)
        for entry_point in metadata.entry_points(group=group)
    ]


def load(entry_point: EntryPointMeta, config: PluginConfig, *, secrets: SecretBox) -> Plugin:
    """Import an entry point's target and instantiate it.

    Only called for a plugin that is actually enabled and about to be used — an
    entry point that is never loaded is never imported, so a disabled plugin costs
    nothing at startup or at request time.
    """
    module_path, _, attr = entry_point.value.partition(":")
    module = __import__(module_path, fromlist=[attr])
    plugin_class = getattr(module, attr)
    return plugin_class(config, secrets=secrets)  # type: ignore[no-any-return]


class PluginRegistry:
    """Resolves configured, enabled plugins to live instances, one per kind.

    Images and voice are process-wide singletons (ADR-0014's DB shape), so this
    caches at most one instance per :class:`~velox_ui.plugins.spec.PluginKind`,
    rebuilt after :meth:`invalidate` — the same "replace drops the cached adapter"
    behaviour ``ProviderRegistry.register`` has.
    """

    __slots__ = ("_instances", "_state")

    def __init__(self, state: AppState) -> None:
        """Bind the registry to the application state it reads configuration from."""
        self._state = state
        self._instances: dict[PluginKind, Plugin | None] = {}

    def invalidate(self, kind: PluginKind) -> None:
        """Drop a cached instance after its configuration changed."""
        self._instances.pop(kind, None)

    def info(self, kind: PluginKind, *, enabled: bool, configured: bool) -> PluginInfo:
        """Describe one kind's discovered entry point for ``GET /api/plugins``."""
        entry_points = discover(kind)
        name = entry_points[0].name if entry_points else kind
        description = {
            "images": "Image generation over an OpenAI-compatible endpoint.",
            "voice": "Speech transcription and synthesis over an OpenAI-compatible endpoint.",
        }[kind]
        return PluginInfo(
            name=name,
            kind=kind,
            enabled=enabled,
            configured=configured,
            description=description,
        )

    @overload
    async def get(self, kind: Literal["images"]) -> ImagePlugin | None: ...
    @overload
    async def get(self, kind: Literal["voice"]) -> VoicePlugin | None: ...

    async def get(self, kind: PluginKind) -> Plugin | None:
        """Return the loaded plugin for a kind, or ``None`` if disabled/unconfigured.

        Reads stored configuration on every call when nothing is cached yet; once
        loaded, the instance is reused until :meth:`invalidate` is called.
        """
        if kind in self._instances:
            return self._instances[kind]

        from velox_ui.db.repositories.plugin_config import PluginConfigRepository

        async with self._state.db.session() as session:
            repository = PluginConfigRepository(session)
            stored = await repository.get(kind)
            if stored is None or not stored.get("enabled"):
                self._instances[kind] = None
                return None
            config = config_from_dict(stored)
            api_key: str | None = None
            auth_ref = stored.get("auth_ref")
            if auth_ref:
                secret = await repository.get_secret(auth_ref)
                if secret is not None:
                    api_key = self._state.secrets.decrypt(
                        secret.nonce, secret.ciphertext, ref=auth_ref
                    )
            config = PluginConfig(
                enabled=config.enabled,
                base_url=config.base_url,
                model=config.model,
                extra=config.extra,
                api_key=api_key,
            )

        entry_points = discover(kind)
        if not entry_points:
            _log.warning("plugin kind %r is enabled but no entry point is registered", kind)
            self._instances[kind] = None
            return None
        plugin = load(entry_points[0], config, secrets=self._state.secrets)
        self._instances[kind] = plugin
        return plugin
