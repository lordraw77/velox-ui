"""Provider configuration: listing, presets, probing, and interface-managed backends.

Reading is open to every signed-in user, because the model picker and the health
badges need it. Changing anything — adding a backend, probing an address, running
discovery — is for administrators: a probe makes this server issue requests to an
address someone typed, which is not something an ordinary account should be able to
point at the network.

Credentials appear only as masked hints, and only to administrators.
"""

from __future__ import annotations

import msgspec
from fastapi import APIRouter, Request, Response

from velox_ui.api.deps import AdminPrincipal, CurrentPrincipal, State
from velox_ui.api.msgspec_io import json_response, read_struct
from velox_ui.errors import NotFoundError
from velox_ui.providers.base import Health, Provider
from velox_ui.providers.presets import load_presets
from velox_ui.providers.registry import ProviderSpec, features
from velox_ui.services.providers import (
    ProviderInput,
    ProviderPatch,
    ProviderService,
    autodiscover,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProbeRequest(msgspec.Struct, forbid_unknown_fields=True):
    """An address to identify before saving it."""

    base_url: str
    api_key: str = ""


@router.get("", summary="List configured providers with their health")
async def list_providers(principal: CurrentPrincipal, state: State) -> Response:
    """Return every configured backend and its reachability.

    An unreachable host is reported as ``down`` rather than raising: that is a normal
    state for a local backend, and the interface renders it as an offline badge.
    """
    await state.discovery_settled()
    registry = state.providers
    health = await registry.health()
    return json_response(
        {
            "providers": [
                _describe(
                    provider,
                    registry.spec(provider.provider_id),
                    health.get(provider.provider_id),
                    admin=principal.is_admin,
                )
                for provider in registry.all()
            ]
        }
    )


@router.get("/presets", summary="The provider preset catalogue")
async def list_presets(principal: CurrentPrincipal) -> Response:
    """Return every preset, for the add-provider form."""
    del principal
    return json_response(
        {
            "presets": [
                {
                    "key": preset.key,
                    "kind": preset.kind,
                    "label": preset.label,
                    "base_url": preset.base_url,
                    "auth": preset.auth,
                    "local": preset.local,
                    "docs_url": preset.docs_url,
                }
                for preset in load_presets().values()
            ]
        }
    )


@router.post("/probe", summary="Identify the backend at an address")
async def probe(request: Request, principal: AdminPrincipal, state: State) -> Response:
    """Report what kind of backend answers at an address, without saving anything."""
    del principal
    from velox_ui.providers.discovery import probe_address

    body = await read_struct(request, ProbeRequest)
    result = await probe_address(state.http, body.base_url, api_key=body.api_key or None)
    return json_response(
        {
            "reachable": result.reachable,
            "kind": result.kind,
            "preset": result.preset,
            "base_url": result.base_url,
            "models": result.models,
            "latency_ms": round(result.latency_ms, 1)
            if result.latency_ms is not None
            else None,
            "detail": result.detail,
        }
    )


@router.post("/autodiscover", summary="Probe the conventional local ports")
async def run_autodiscovery(principal: AdminPrincipal, state: State) -> Response:
    """Probe loopback ports and register any backend not already configured.

    Loopback only. Scanning a network stays an explicit, per-address action (probe).
    """
    del principal
    return json_response({"found": await autodiscover(state)})


@router.post("", status_code=201, summary="Add a provider")
async def create_provider(
    request: Request, principal: AdminPrincipal, state: State
) -> Response:
    """Store and register a backend. The credential is encrypted before it is written."""
    del principal
    body = await read_struct(request, ProviderInput)
    spec = await ProviderService(state).create(body)
    return json_response(_describe_spec(state, spec), status_code=201)


@router.patch("/{provider_id}", summary="Change a provider")
async def update_provider(
    provider_id: str, request: Request, principal: AdminPrincipal, state: State
) -> Response:
    """Change an interface-managed backend's name, address or credential."""
    del principal
    body = await read_struct(request, ProviderPatch)
    spec = await ProviderService(state).update(provider_id, body)
    return json_response(_describe_spec(state, spec))


@router.delete("/{provider_id}", status_code=204, summary="Remove a provider")
async def delete_provider(
    provider_id: str, principal: AdminPrincipal, state: State
) -> Response:
    """Remove an interface-managed backend and its stored credential."""
    del principal
    await ProviderService(state).delete(provider_id)
    return Response(status_code=204)


@router.post("/{provider_id}/refresh", summary="Rediscover a provider's models")
async def refresh_provider(
    provider_id: str, principal: CurrentPrincipal, state: State
) -> Response:
    """Drop cached model data for one backend and list its models afresh.

    Raises:
        NotFoundError: If no such provider is configured.
    """
    del principal
    provider = state.providers.get(provider_id)
    if provider is None:
        raise NotFoundError("No such provider.")
    state.providers.invalidate(provider_id)
    models = await provider.list_models(refresh=True)
    return json_response({"provider_id": provider_id, "models": len(models)})


def _describe_spec(state: object, spec: ProviderSpec) -> dict[str, object]:
    """Describe a just-changed provider, without probing its health."""
    from velox_ui.state import AppState

    assert isinstance(state, AppState)  # noqa: S101 - narrowing for the type checker
    provider = state.providers.get(spec.provider_id)
    assert provider is not None  # noqa: S101 - it was registered a moment ago
    return _describe(provider, spec, None, admin=True)


def _describe(
    provider: Provider, spec: ProviderSpec | None, health: Health | None, *, admin: bool
) -> dict[str, object]:
    """Render one provider for the API."""
    return {
        "provider_id": provider.provider_id,
        "name": spec.label if spec else provider.provider_id,
        "kind": spec.kind if spec else None,
        "preset": spec.preset if spec else None,
        "origin": spec.origin if spec else "config",
        "editable": bool(spec and spec.origin == "ui"),
        "is_local": provider.is_local,
        "base_url": getattr(provider, "base_url", None),
        "credential_hint": spec.credential_hint if spec and admin else None,
        "features": features(provider),
        "supported_params": sorted(provider.supported_params),
        "health": health,
    }
