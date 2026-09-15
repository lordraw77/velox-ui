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
from typing import TYPE_CHECKING, Literal, cast, overload

from velox_ui.plugins.errors import PluginDisabledError
from velox_ui.plugins.spec import (
    ImagePlugin,
    Plugin,
    PluginConfig,
    PluginInfo,
    PluginKind,
    ToolPlugin,
    VoicePlugin,
    config_from_dict,
)

if TYPE_CHECKING:
    from typing import Any

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

    __slots__ = ("_instances", "_state", "_tool_plugins")

    def __init__(self, state: AppState) -> None:
        """Bind the registry to the application state it reads configuration from."""
        self._state = state
        self._instances: dict[PluginKind, Plugin | None] = {}
        self._tool_plugins: list[ToolPlugin] | None = None

    def invalidate(self, kind: PluginKind) -> None:
        """Drop a cached instance after its configuration changed."""
        self._instances.pop(kind, None)
        if kind == "tools":
            self._tool_plugins = None

    def info(self, kind: PluginKind, *, enabled: bool, configured: bool) -> PluginInfo:
        """Describe one kind's discovered entry point for ``GET /api/plugins``."""
        entry_points = discover(kind)
        name = entry_points[0].name if entry_points else kind
        description = {
            "images": "Image generation over an OpenAI-compatible endpoint.",
            "voice": "Speech transcription and synthesis over an OpenAI-compatible endpoint.",
            "tools": (
                "Builtin tools a model can call mid-turn: the date and time, plus "
                "web search and browsing once a SearXNG address is set below."
            ),
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
    @overload
    async def get(self, kind: Literal["tools"]) -> ToolPlugin | None: ...

    async def get(self, kind: PluginKind) -> Plugin | None:
        """Return the loaded plugin for a kind, or ``None`` if disabled/unconfigured.

        Reads stored configuration on every call when nothing is cached yet; once
        loaded, the instance is reused until :meth:`invalidate` is called.

        For ``"tools"`` this returns the *first* registered plugin only; that group
        is a set rather than a singleton, so callers that want every tool should
        use :meth:`tool_plugins`.
        """
        if kind in self._instances:
            return self._instances[kind]

        config = await self._config_for(kind)
        if config is None:
            self._instances[kind] = None
            return None

        entry_points = discover(kind)
        if not entry_points:
            _log.warning("plugin kind %r is enabled but no entry point is registered", kind)
            self._instances[kind] = None
            return None
        plugin = load(entry_points[0], config, secrets=self._state.secrets)
        self._instances[kind] = plugin
        return plugin

    async def tool_plugins(self) -> list[ToolPlugin]:
        """Every enabled plugin in the ``velox_ui.tools`` group.

        Unlike ``images``/``voice`` — one configured backend each — the tools group
        is genuinely plural: the date/time plugin answers from the host and needs
        no configuration, while the websearch plugin needs a SearXNG address, and
        both should be able to contribute tools to the same turn. A plugin that
        lacks the configuration it needs reports no tools rather than failing, so
        enabling the group with nothing configured still gets you the ones that
        need nothing.
        """
        if self._tool_plugins is not None:
            return self._tool_plugins

        config = await self._config_for("tools")
        if config is None:
            self._tool_plugins = []
            return self._tool_plugins

        plugins: list[ToolPlugin] = []
        for entry_point in discover("tools"):
            try:
                plugins.append(
                    cast(ToolPlugin, load(entry_point, config, secrets=self._state.secrets))
                )
            except Exception:
                # One broken third-party plugin must not take the rest of the
                # group — and the turn that wanted them — down with it.
                _log.exception("tools plugin %r failed to load", entry_point.name)
        self._tool_plugins = plugins
        return plugins

    async def _config_for(self, kind: PluginKind) -> PluginConfig | None:
        """Load and decrypt a kind's stored config, or ``None`` when it is disabled."""
        from velox_ui.db.repositories.plugin_config import PluginConfigRepository

        async with self._state.db.session() as session:
            repository = PluginConfigRepository(session)
            stored = await repository.get(kind)
            if stored is None or not stored.get("enabled"):
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
            return PluginConfig(
                enabled=config.enabled,
                base_url=config.base_url,
                model=config.model,
                extra=config.extra,
                api_key=api_key,
            )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Run a builtin tool by name, on whichever tools plugin provides it.

        Raises:
            PluginDisabledError: If no enabled plugin offers a tool by that name.
        """
        for plugin in await self.tool_plugins():
            if any(tool.name == name for tool in plugin.tools()):
                return await plugin.call(name, arguments)
        raise PluginDisabledError(f"No enabled plugin provides the tool {name!r}.")
