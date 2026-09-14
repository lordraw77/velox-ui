"""Provider registry: turns provider specs into live adapter instances.

A spec is where a backend came from and how to reach it — configuration, autodiscovery
or the interface — and nothing more. The adapter is built from it on first use, so
registering a backend opens no connection and imports nothing: an instance that is
never asked about a backend never pays for its adapter module.

This is also the one place that maps a provider *kind* to an adapter class. Everything
else — routes, services, the interface — asks an adapter what it can do
(:func:`features`, ``supported_params``) instead of asking what it is.

Model discovery is cached in-process with a short TTL: probing every configured host on
every page load would defeat the point of a local-first client, and a listing a few
seconds stale is a fine trade against that.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from velox_ui.providers.base import (
    Health,
    LocalModelAdmin,
    ModelInfo,
    ModelInspector,
    Provider,
)
from velox_ui.providers.errors import ModelNotFound

if TYPE_CHECKING:  # pragma: no cover - typing only. Importing httpx or the adapters
    # here would pull roughly a tenth of the cold-start budget into every startup,
    # including instances that never talk to a backend.
    import httpx

__all__ = ["ProviderKind", "ProviderRegistry", "ProviderSpec", "ResolvedModel", "features"]

_MODEL_CACHE_TTL_S = 30.0

type ProviderKind = Literal["ollama", "llamacpp", "openai_compat", "anthropic"]
type ProviderOrigin = Literal["config", "autodiscovered", "ui"]


@dataclass(frozen=True)
class ProviderSpec:
    """How to reach one backend, and where that knowledge came from.

    Attributes:
        provider_id: The first half of every ``model_ref`` served by this backend.
        kind: Which adapter drives it.
        base_url: Its address.
        preset: Preset key; required for ``openai_compat``.
        name: Label for the interface. Defaults to the id.
        origin: ``config`` and ``autodiscovered`` backends are rebuilt at every start
            and are read-only in the interface; ``ui`` backends are stored.
        credential: Returns the API key when called. A callable rather than the value,
            so a credential stored encrypted is decrypted only when an adapter is
            actually built, and so the plaintext never sits in a dataclass ``repr``.
        credential_hint: Masked form of the credential, for display.
    """

    provider_id: str
    kind: ProviderKind
    base_url: str
    preset: str | None = None
    name: str | None = None
    origin: ProviderOrigin = "config"
    credential: Callable[[], str | None] | None = field(default=None, repr=False, compare=False)
    credential_hint: str | None = None

    @property
    def label(self) -> str:
        """The name to show."""
        return self.name or self.provider_id


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


def features(provider: Provider) -> tuple[str, ...]:
    """List the optional operations an adapter supports.

    The interface enables controls from this list rather than trying an operation and
    reporting that it failed.
    """
    found: list[str] = []
    if isinstance(provider, ModelInspector):
        found += ["show", "running"]
    if isinstance(provider, LocalModelAdmin):
        found += ["pull", "create", "delete", "copy", "unload"]
    return tuple(found)


class ProviderRegistry:
    """Holds every configured provider for this process."""

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient]) -> None:
        """Start empty.

        Args:
            client_factory: Returns the shared HTTP client. It is a factory rather
                than the client itself so that registering a provider does not force
                the client — and the httpx import behind it — into existence during
                startup.
        """
        self._client_factory = client_factory
        self._specs: dict[str, ProviderSpec] = {}
        self._instances: dict[str, Provider] = {}
        self._cache: dict[str, _CachedModels] = {}
        self._cache_lock = asyncio.Lock()

    def add(self, provider: Provider) -> None:
        """Register an already-constructed provider instance. Used by tests."""
        self._instances[provider.provider_id] = provider

    def register(self, spec: ProviderSpec) -> None:
        """Register or replace a backend. The adapter is built on first use.

        Replacing a spec discards the previous adapter and its cached model list, so an
        edited address or credential takes effect on the next request.
        """
        self._specs[spec.provider_id] = spec
        self._instances.pop(spec.provider_id, None)
        self._cache.pop(spec.provider_id, None)

    def remove(self, provider_id: str) -> bool:
        """Forget a backend. Returns whether it was registered."""
        known = provider_id in self._specs or provider_id in self._instances
        self._specs.pop(provider_id, None)
        self._instances.pop(provider_id, None)
        self._cache.pop(provider_id, None)
        return known

    def spec(self, provider_id: str) -> ProviderSpec | None:
        """Return how a backend was configured, without building its adapter."""
        return self._specs.get(provider_id)

    def specs(self) -> list[ProviderSpec]:
        """Every registered spec, in registration order."""
        return list(self._specs.values())

    def invalidate(self, provider_id: str) -> None:
        """Drop a backend's cached model list, after something changed what it has."""
        self._cache.pop(provider_id, None)

    @property
    def provider_ids(self) -> list[str]:
        """Every configured provider id in registration order, without constructing anything.

        Registration order, not construction order: which adapter happened to be built
        first depends on which request came first, and the model picker must not
        reorder itself depending on that.
        """
        return [*self._specs, *(key for key in self._instances if key not in self._specs)]

    def get(self, provider_id: str) -> Provider | None:
        """Return a provider, constructing its adapter if this is the first use."""
        if (existing := self._instances.get(provider_id)) is not None:
            return existing
        spec = self._specs.get(provider_id)
        if spec is None:
            return None
        provider = self._build(spec)
        self._instances[provider_id] = provider
        return provider

    def _build(self, spec: ProviderSpec) -> Provider:
        """Construct the adapter for a spec. The only branch on provider kind."""
        client = self._client_factory()
        api_key = spec.credential() if spec.credential is not None else None
        if spec.kind == "ollama":
            from velox_ui.providers.ollama import OllamaProvider

            return OllamaProvider(spec.provider_id, spec.base_url, client)
        if spec.kind == "llamacpp":
            from velox_ui.providers.llamacpp import LlamaCppProvider

            return LlamaCppProvider(spec.provider_id, spec.base_url, client, api_key=api_key)
        if spec.kind == "anthropic":
            from velox_ui.providers.anthropic import AnthropicProvider

            return AnthropicProvider(spec.provider_id, spec.base_url, client, api_key=api_key)

        from velox_ui.providers.openai_compat import OpenAICompatProvider
        from velox_ui.providers.presets import get_preset

        preset = get_preset(spec.preset or "custom")
        if preset is None:
            raise ValueError(
                f"provider {spec.provider_id!r} names unknown preset {spec.preset!r}"
            )
        return OpenAICompatProvider(
            spec.provider_id, spec.base_url, client, preset=preset, api_key=api_key
        )

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

        Providers are asked concurrently, and one that cannot be reached contributes an
        empty list rather than failing the call: an offline host must neither blank
        out every other provider's models nor make the picker wait for its timeout
        before showing them (ADR-0008).
        """
        providers = self.all()
        listings = await asyncio.gather(
            *(self._models_for(provider, refresh=refresh) for provider in providers)
        )
        return {
            provider.provider_id: models
            for provider, models in zip(providers, listings, strict=True)
        }

    async def _models_for(self, provider: Provider, *, refresh: bool) -> list[ModelInfo]:
        """Return one provider's models, using the short-lived cache unless refreshed."""
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
                ``"ollama-0:llama3.2"``.

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
