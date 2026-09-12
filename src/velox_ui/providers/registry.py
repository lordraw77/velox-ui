"""Provider registry: turns configuration into live adapter instances.

Phase 2 configures providers from settings (``VELOX_PROVIDER_OLLAMA_HOSTS`` and
``VELOX_PROVIDER_LLAMACPP_HOSTS``), not yet from the database-backed CRUD API the
design describes — that lands with local model management in phase 4. The registry
itself does not care where a :class:`~velox_ui.providers.base.Provider` came from, so
adding the database-backed path later does not change how the chat service resolves
one.

Model discovery is cached in-process with a short TTL: probing every configured host on
every page load would defeat the point of a local-first client, and a stale entry for a
few seconds is a fine trade against that (docs/design/00-overview.md, provider
discovery requirement).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from velox_ui.providers.base import Health, ModelInfo, Provider
from velox_ui.providers.errors import ModelNotFound

if TYPE_CHECKING:  # pragma: no cover - typing only. Importing httpx or the adapters
    # here would pull roughly a tenth of the cold-start budget into every startup,
    # including instances that never talk to a backend.
    import httpx

__all__ = ["ProviderRegistry", "ResolvedModel"]

_MODEL_CACHE_TTL_S = 30.0


@dataclass
class _CachedModels:
    """Cached discovery result for one provider."""

    models: list[ModelInfo]
    fetched_at: float


@dataclass
class ResolvedModel:
    """A model reference resolved to a live provider.

    Attributes:
        provider: The adapter to call.
        model_key: The model key as that backend names it.
    """

    provider: Provider
    model_key: str


class ProviderRegistry:
    """Holds every configured provider instance for this process.

    Args:
        client: The shared HTTP client every adapter is built with.
    """

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient]) -> None:
        """Start empty.

        Args:
            client_factory: Returns the shared HTTP client. It is a factory rather
                than the client itself so that registering a provider does not force
                the client — and the httpx import behind it — into existence during
                startup.
        """
        self._client_factory = client_factory
        self._specs: dict[str, tuple[str, str]] = {}
        self._instances: dict[str, Provider] = {}
        self._cache: dict[str, _CachedModels] = {}
        self._cache_lock = asyncio.Lock()

    def add(self, provider: Provider) -> None:
        """Register an already-constructed provider instance."""
        self._instances[provider.provider_id] = provider

    def add_ollama(self, provider_id: str, base_url: str) -> None:
        """Configure an Ollama host. The adapter is built on first use."""
        self._specs[provider_id] = ("ollama", base_url)

    def add_llamacpp(self, provider_id: str, base_url: str) -> None:
        """Configure a llama.cpp host. The adapter is built on first use."""
        self._specs[provider_id] = ("llamacpp", base_url)

    @property
    def provider_ids(self) -> list[str]:
        """Every configured provider id, without constructing anything."""
        return [*self._instances, *(key for key in self._specs if key not in self._instances)]

    def get(self, provider_id: str) -> Provider | None:
        """Return a provider, constructing its adapter if this is the first use."""
        if (existing := self._instances.get(provider_id)) is not None:
            return existing
        spec = self._specs.get(provider_id)
        if spec is None:
            return None
        kind, base_url = spec
        client = self._client_factory()
        provider: Provider
        if kind == "ollama":
            from velox_ui.providers.ollama import OllamaProvider

            provider = OllamaProvider(provider_id, base_url, client)
        else:
            from velox_ui.providers.llamacpp import LlamaCppProvider

            provider = LlamaCppProvider(provider_id, base_url, client)
        self._instances[provider_id] = provider
        return provider

    def all(self) -> list[Provider]:
        """Return every configured provider, local backends first (ADR-0008)."""
        providers = [
            provider
            for provider in (self.get(provider_id) for provider_id in self.provider_ids)
            if provider is not None
        ]
        return sorted(providers, key=lambda provider: not provider.is_local)

    async def list_models(self, *, refresh: bool = False) -> dict[str, list[ModelInfo]]:
        """List models for every provider, keyed by ``provider_id``.

        A provider that cannot be reached contributes an empty list rather than
        failing the whole call: one offline local host must not blank out every other
        provider's models (ADR-0008).
        """
        results: dict[str, list[ModelInfo]] = {}
        for provider in self.all():
            results[provider.provider_id] = await self._models_for(provider, refresh=refresh)
        return results

    async def _models_for(self, provider: Provider, *, refresh: bool) -> list[ModelInfo]:
        """Return one provider's models, using the short-lived cache unless refreshed."""
        cached = self._cache.get(provider.provider_id)
        if (
            not refresh
            and cached is not None
            and time.monotonic() - cached.fetched_at < _MODEL_CACHE_TTL_S
        ):
            return cached.models

        async with self._cache_lock:
            cached = self._cache.get(provider.provider_id)
            if (
                not refresh
                and cached is not None
                and time.monotonic() - cached.fetched_at < _MODEL_CACHE_TTL_S
            ):
                return cached.models
            try:
                models = await provider.list_models(refresh=refresh)
            except Exception:
                return cached.models if cached is not None else []
            self._cache[provider.provider_id] = _CachedModels(
                models=models, fetched_at=time.monotonic()
            )
            return models

    async def health(self) -> dict[str, Health]:
        """Probe every provider concurrently."""
        providers = self.all()
        results = await asyncio.gather(*(provider.health() for provider in providers))
        return {
            provider.provider_id: result
            for provider, result in zip(providers, results, strict=True)
        }

    async def resolve(self, model_ref: str) -> ResolvedModel:
        """Resolve a ``"provider_id:model_key"`` reference to a live provider.

        Args:
            model_ref: A reference as stored on a chat or message, e.g.
                ``"ollama-local:llama3.2"``.

        Returns:
            The provider and model key to call.

        Raises:
            ModelNotFound: If the reference is malformed or names an unknown
                provider. Whether the model itself exists on that provider is left to
                the call that actually uses it — asking twice would cost a round trip
                the streaming path does not need to pay.
        """
        provider_id, separator, model_key = model_ref.partition(":")
        if not separator:
            raise ModelNotFound(
                f"{model_ref!r} is not a valid model reference; expected 'provider:model'.",
                provider_id="unknown",
            )
        provider = self.get(provider_id)
        if provider is None:
            raise ModelNotFound(
                f"No provider configured with id {provider_id!r}.", provider_id=provider_id
            )
        return ResolvedModel(provider=provider, model_key=model_key)
