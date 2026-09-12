"""Model discovery across every configured provider.

Local models are listed first and priced at exactly zero, because on a self-hosted
instance they are the default rather than the fallback (ADR-0008). A provider that
cannot be reached contributes nothing and does not fail the request: one Ollama host
being switched off must not empty the model picker.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.api.msgspec_io import json_response

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", summary="List every available model")
async def list_models(
    principal: CurrentPrincipal,
    state: State,
    refresh: Annotated[bool, Query()] = False,
) -> Response:
    """Return the unified model list, grouped by provider, local providers first."""
    del principal
    discovered = await state.providers.list_models(refresh=refresh)
    groups = []
    for provider in state.providers.all():
        models = discovered.get(provider.provider_id, [])
        groups.append(
            {
                "provider_id": provider.provider_id,
                "is_local": provider.is_local,
                "models": [
                    {
                        "key": model.key,
                        "model_ref": f"{provider.provider_id}:{model.key}",
                        "display_name": model.display_name,
                        "family": model.family,
                        "loaded": model.loaded,
                        "capabilities": model.capabilities,
                    }
                    for model in models
                ],
            }
        )
    return json_response({"providers": groups})


@router.get("/providers", summary="List configured providers with their health")
async def list_providers(principal: CurrentPrincipal, state: State) -> Response:
    """Return every configured backend and its cached reachability.

    An unreachable host is reported as ``down`` rather than raising: that is a normal
    state for a local backend, and the UI renders it as an offline badge.
    """
    del principal
    health = await state.providers.health()
    return json_response(
        {
            "providers": [
                {
                    "provider_id": provider.provider_id,
                    "is_local": provider.is_local,
                    "base_url": getattr(provider, "base_url", None),
                    "health": health.get(provider.provider_id),
                }
                for provider in state.providers.all()
            ]
        }
    )
