"""Where providers come from, and how the interface changes them.

Three sources feed the registry, in this order of precedence:

1. **Configuration** — ``velox.toml`` and ``VELOX_PROVIDER_*`` variables. Rebuilt at
   every start and read-only in the interface, so an edited file is never silently
   overridden by a stale copy in the database.
2. **The interface** — stored in the ``provider`` table, with credentials encrypted in
   ``secret`` (ADR-0013). A stored provider whose id collides with a configured one is
   skipped with a warning: configuration wins.
3. **Autodiscovery** — loopback probes at start when nothing is configured at all.

Credentials never leave this module in clear except inside an adapter's request
headers. The API sees a masked hint; the registry sees a callable that decrypts on the
one occasion an adapter is built.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from urllib.parse import urlsplit

import msgspec

from velox_ui.db.repositories.providers import ProviderRepository
from velox_ui.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from velox_ui.providers.presets import Preset, get_preset, load_presets
from velox_ui.providers.registry import ProviderSpec
from velox_ui.security.crypto import SecretBox, mask_secret
from velox_ui.settings import ProviderSettings
from velox_ui.state import AppState

__all__ = [
    "PROVIDER_ID_PATTERN",
    "ProviderInput",
    "ProviderPatch",
    "ProviderService",
    "autodiscover",
    "config_specs",
]

_log = logging.getLogger("velox.providers")

PROVIDER_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"
_PROVIDER_ID = re.compile(PROVIDER_ID_PATTERN)


class ProviderInput(msgspec.Struct, forbid_unknown_fields=True):
    """A provider to add from the interface.

    Attributes:
        preset: Preset key.
        base_url: Address; empty means the preset's default.
        id: Provider id; empty means one derived from the preset.
        name: Label; empty means the preset's label.
        api_key: Credential, stored encrypted. Empty for none.
    """

    preset: str
    base_url: str = ""
    id: str = ""
    name: str = ""
    api_key: str = ""


class ProviderPatch(msgspec.Struct, forbid_unknown_fields=True):
    """Changes to a stored provider. ``None`` leaves a field as it is.

    Attributes:
        name: New label.
        base_url: New address.
        api_key: New credential; an empty string removes it.
    """

    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


def config_specs(settings: ProviderSettings) -> list[ProviderSpec]:
    """Build the specs for every backend named in configuration.

    Ids are stable for a given configuration: explicit ids first, then
    ``<preset>-<n>`` counting per preset, skipping any id already taken.
    """
    specs: list[ProviderSpec] = []
    taken = {endpoint.id for endpoint in settings.endpoints if endpoint.id}

    def next_id(prefix: str) -> str:
        index = 0
        while f"{prefix}-{index}" in taken:
            index += 1
        taken.add(f"{prefix}-{index}")
        return f"{prefix}-{index}"

    presets = load_presets()
    for base_url in settings.ollama_hosts:
        specs.append(
            ProviderSpec(
                provider_id=next_id("ollama"),
                kind="ollama",
                base_url=base_url,
                preset="ollama",
                name=presets["ollama"].label,
            )
        )
    for base_url in settings.llamacpp_hosts:
        specs.append(
            ProviderSpec(
                provider_id=next_id("llamacpp"),
                kind="llamacpp",
                base_url=base_url,
                preset="llamacpp",
                name=presets["llamacpp"].label,
            )
        )
    for endpoint in settings.endpoints:
        preset = presets[endpoint.preset]  # validated when settings were loaded
        api_key = endpoint.api_key or None
        specs.append(
            ProviderSpec(
                provider_id=endpoint.id or next_id(endpoint.preset.replace("_", "-")),
                kind=preset.kind,
                base_url=endpoint.base_url or preset.base_url,
                preset=endpoint.preset,
                name=endpoint.name or preset.label,
                origin="config",
                credential=_constant(api_key) if api_key else None,
                credential_hint=mask_secret(api_key) if api_key else None,
            )
        )
    return specs


class ProviderService:
    """Loads stored providers and applies changes made in the interface.

    Args:
        state: Application state.
    """

    def __init__(self, state: AppState) -> None:
        """Bind the service to the application state."""
        self._state = state

    async def load_stored(self) -> int:
        """Register every stored provider. Returns how many were registered."""
        registry = self._state.providers
        async with self._state.db.session() as session:
            repository = ProviderRepository(session)
            rows = await repository.list()
            loaded = 0
            for row in rows:
                existing = registry.spec(row.id)
                if existing is not None and existing.origin != "ui":
                    _log.warning(
                        "stored provider shadowed by configuration",
                        extra={"provider": row.id},
                    )
                    continue
                secret = await repository.get_secret(row.auth_ref) if row.auth_ref else None
                registry.register(
                    ProviderSpec(
                        provider_id=row.id,
                        kind=row.kind,  # type: ignore[arg-type]
                        base_url=row.base_url,
                        preset=row.preset,
                        name=row.name,
                        origin="ui",
                        credential=(
                            _stored_credential(
                                self._state.secrets, secret.ref, secret.nonce, secret.ciphertext
                            )
                            if secret is not None
                            else None
                        ),
                        credential_hint=secret.hint if secret is not None else None,
                    )
                )
                loaded += 1
        return loaded

    async def create(self, body: ProviderInput) -> ProviderSpec:
        """Validate, store and register a provider.

        Raises:
            ValidationError: For an unknown preset, a bad address or id, or a missing
                credential the preset requires.
            ConflictError: If the id is already in use.
        """
        preset = _preset_or_error(body.preset)
        base_url = _normalise_url(body.base_url or preset.base_url)
        provider_id = body.id.strip() or self._free_id(body.preset.replace("_", "-"))
        if not _PROVIDER_ID.match(provider_id):
            raise ValidationError(
                "The provider id must be lowercase letters, digits and hyphens, "
                "at most 40 characters."
            )
        if self._state.providers.spec(provider_id) is not None:
            raise ConflictError(f"A provider with id {provider_id!r} already exists.")
        api_key = body.api_key.strip()
        if preset.auth == "required" and not api_key:
            raise ValidationError(f"{preset.label} requires an API key.")

        ref = f"provider:{provider_id}:api_key" if api_key else None
        async with self._state.db.write() as session:
            repository = ProviderRepository(session)
            if await repository.get(provider_id) is not None:
                raise ConflictError(f"A provider with id {provider_id!r} already exists.")
            await repository.create(
                provider_id=provider_id,
                name=body.name.strip() or preset.label,
                kind=preset.kind,
                preset=preset.key,
                base_url=base_url,
                is_local=preset.is_local_for(base_url),
                auth_ref=ref,
            )
            spec_credential = await self._store_secret(repository, ref, api_key)

        spec = ProviderSpec(
            provider_id=provider_id,
            kind=preset.kind,
            base_url=base_url,
            preset=preset.key,
            name=body.name.strip() or preset.label,
            origin="ui",
            credential=spec_credential,
            credential_hint=mask_secret(api_key) if api_key else None,
        )
        self._state.providers.register(spec)
        _log.info("provider added", extra={"provider": provider_id, "preset": preset.key})
        return spec

    async def update(self, provider_id: str, patch: ProviderPatch) -> ProviderSpec:
        """Apply changes to a stored provider and re-register it.

        Raises:
            NotFoundError: If no such provider exists.
            ForbiddenError: If it comes from configuration or autodiscovery.
            ValidationError: For a bad address.
        """
        current = self._editable(provider_id)
        preset = _preset_or_error(current.preset or "custom")
        base_url = (
            _normalise_url(patch.base_url) if patch.base_url is not None else current.base_url
        )
        name = (
            patch.name.strip()
            if patch.name is not None and patch.name.strip()
            else current.name
        )

        credential = current.credential
        hint = current.credential_hint
        async with self._state.db.write() as session:
            repository = ProviderRepository(session)
            row = await repository.get(provider_id)
            if row is None:
                raise NotFoundError("No such provider.")
            row.name = name or row.name
            row.base_url = base_url
            row.is_local = preset.is_local_for(base_url)
            if patch.api_key is not None:
                api_key = patch.api_key.strip()
                ref = f"provider:{provider_id}:api_key"
                if api_key:
                    credential = await self._store_secret(repository, ref, api_key)
                    hint = mask_secret(api_key)
                    row.auth_ref = ref
                else:
                    await repository.delete_secret(ref)
                    credential, hint, row.auth_ref = None, None, None

        spec = ProviderSpec(
            provider_id=provider_id,
            kind=current.kind,
            base_url=base_url,
            preset=current.preset,
            name=name,
            origin="ui",
            credential=credential,
            credential_hint=hint,
        )
        self._state.providers.register(spec)
        return spec

    async def delete(self, provider_id: str) -> None:
        """Remove a stored provider and its credential.

        Raises:
            NotFoundError: If no such provider exists.
            ForbiddenError: If it comes from configuration or autodiscovery.
        """
        self._editable(provider_id)
        async with self._state.db.write() as session:
            await ProviderRepository(session).delete(provider_id)
        self._state.providers.remove(provider_id)
        _log.info("provider removed", extra={"provider": provider_id})

    def _editable(self, provider_id: str) -> ProviderSpec:
        """Return a spec the interface may change, or raise the reason it may not."""
        spec = self._state.providers.spec(provider_id)
        if spec is None:
            raise NotFoundError("No such provider.")
        if spec.origin != "ui":
            where = (
                "velox.toml or the environment" if spec.origin == "config" else "autodiscovery"
            )
            raise ForbiddenError(
                f"Provider {provider_id!r} comes from {where}; change it there."
            )
        return spec

    def _free_id(self, prefix: str) -> str:
        """The first ``<prefix>``, ``<prefix>-2``, ... not already registered."""
        registry = self._state.providers
        if registry.spec(prefix) is None and _PROVIDER_ID.match(prefix):
            return prefix
        index = 2
        while registry.spec(f"{prefix}-{index}") is not None:
            index += 1
        return f"{prefix}-{index}"

    async def _store_secret(
        self, repository: ProviderRepository, ref: str | None, api_key: str
    ) -> Callable[[], str | None] | None:
        """Encrypt and store a credential, returning the lazy accessor for the spec."""
        if ref is None or not api_key:
            return None
        nonce, ciphertext = self._state.secrets.encrypt(api_key, ref=ref)
        await repository.put_secret(
            ref, nonce=nonce, ciphertext=ciphertext, hint=mask_secret(api_key)
        )
        return _stored_credential(self._state.secrets, ref, nonce, ciphertext)


async def autodiscover(state: AppState) -> list[dict[str, object]]:
    """Probe the conventional loopback ports and register what answers.

    A backend already registered at the same address is reported but not added twice.

    Returns:
        One entry per backend found: ``provider_id``, ``preset``, ``base_url`` and
        whether it was ``added`` by this call.
    """
    # The client first: building it imports httpx, and doing that in a worker thread is
    # the point (see AppState.http_client). The discovery module imports httpx too, so
    # it is only imported once httpx is already loaded and the import is free.
    client = await state.http_client()
    from velox_ui.providers.discovery import probe_local_backends

    registry = state.providers
    known_urls = {spec.base_url.rstrip("/") for spec in registry.specs()}
    presets = load_presets()
    report: list[dict[str, object]] = []
    for found in await probe_local_backends(client):
        if found.base_url.rstrip("/") in known_urls:
            existing = next(
                spec for spec in registry.specs() if spec.base_url.rstrip("/") == found.base_url
            )
            report.append(
                {
                    "provider_id": existing.provider_id,
                    "preset": found.preset,
                    "base_url": found.base_url,
                    "added": False,
                }
            )
            continue
        prefix = found.preset.replace("_", "-")
        index = 0
        while registry.spec(f"{prefix}-{index}") is not None:
            index += 1
        spec = ProviderSpec(
            provider_id=f"{prefix}-{index}",
            kind=presets[found.preset].kind,
            base_url=found.base_url,
            preset=found.preset,
            name=presets[found.preset].label,
            origin="autodiscovered",
        )
        registry.register(spec)
        known_urls.add(found.base_url)
        report.append(
            {
                "provider_id": spec.provider_id,
                "preset": found.preset,
                "base_url": found.base_url,
                "added": True,
            }
        )
    return report


def _constant(value: str) -> Callable[[], str | None]:
    """Wrap a configuration-sourced credential in the accessor a spec expects."""

    def reveal() -> str | None:
        return value

    return reveal


def _stored_credential(
    secrets: SecretBox, ref: str, nonce: bytes, ciphertext: bytes
) -> Callable[[], str | None]:
    """Return a callable that decrypts a stored credential when invoked.

    A credential that no longer decrypts — the secret key changed since it was saved —
    yields ``None`` and a warning naming the provider, rather than an exception: the
    adapter is built from inside model listing, and one unreadable key must not take
    every other provider's models down with it. The backend then rejects the request
    and the interface shows that the credential needs re-entering.
    """

    def reveal() -> str | None:
        try:
            return secrets.decrypt(nonce, ciphertext, ref=ref)
        except Exception as exc:
            _log.warning(
                "stored credential cannot be decrypted", extra={"ref": ref, "detail": str(exc)}
            )
            return None

    return reveal


def _preset_or_error(key: str) -> Preset:
    """Look up a preset, raising a readable validation error for an unknown one."""
    preset = get_preset(key)
    if preset is None:
        raise ValidationError(
            f"Unknown preset {key!r}. Known presets: {', '.join(sorted(load_presets()))}."
        )
    return preset


def _normalise_url(value: str) -> str:
    """Validate an address and strip a trailing slash.

    Raises:
        ValidationError: If it is not an absolute http or https URL with a host.
    """
    candidate = value.strip().rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError("The address must be an http:// or https:// URL with a host.")
    if parts.username or parts.password:
        raise ValidationError(
            "Put credentials in the API key field, not in the address, so they are stored "
            "encrypted and never shown."
        )
    return candidate
