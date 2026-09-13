"""Model discovery and per-model parameters.

Local models are listed first and priced at exactly zero, because on a self-hosted
instance they are the default rather than the fallback (ADR-0008). A provider that
cannot be reached contributes nothing and does not fail the request: one Ollama host
being switched off must not empty the model picker.

Each provider group carries ``supported_params`` and ``features``, so the interface
shows the controls a backend actually has instead of trying one and reporting that it
failed.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.api.msgspec_io import json_response, read_struct
from velox_ui.providers.registry import features
from velox_ui.services.model_params import ModelParamsInput

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", summary="List every available model")
async def list_models(
    principal: CurrentPrincipal,
    state: State,
    refresh: Annotated[bool, Query()] = False,
) -> Response:
    """Return the unified model list, grouped by provider, local providers first."""
    del principal
    await state.discovery_settled()
    registry = state.providers
    discovered = await registry.list_models(refresh=refresh)
    groups = []
    for provider in registry.all():
        spec = registry.spec(provider.provider_id)
        models = discovered.get(provider.provider_id, [])
        groups.append(
            {
                "provider_id": provider.provider_id,
                "name": spec.label if spec else provider.provider_id,
                "is_local": provider.is_local,
                "supported_params": sorted(provider.supported_params),
                "features": features(provider),
                "models": [
                    {
                        "key": model.key,
                        "model_ref": f"{provider.provider_id}:{model.key}",
                        "display_name": model.display_name,
                        "family": model.family,
                        "loaded": model.loaded,
                        "parameter_size": model.parameter_size,
                        "capabilities": model.capabilities,
                    }
                    for model in models
                ],
            }
        )
    return json_response({"providers": groups})


@router.get("/model-params/{model_ref:path}", summary="Saved parameters for a model")
async def get_model_params(
    model_ref: str, principal: CurrentPrincipal, state: State
) -> Response:
    """Return the caller's saved parameters for one model and what its backend accepts.

    Raises:
        ModelNotFound: If the reference names no configured provider.
    """
    resolved = await state.providers.resolve(model_ref)
    saved = await state.model_params.get(principal.user_id, model_ref)
    return json_response(_params_payload(model_ref, saved, resolved.provider.supported_params))


@router.put("/model-params/{model_ref:path}", summary="Save parameters for a model")
async def put_model_params(
    model_ref: str, request: Request, principal: CurrentPrincipal, state: State
) -> Response:
    """Replace the caller's saved parameters for one model.

    Parameters the model's backend does not accept are rejected rather than stored:
    saving ``num_gpu`` for a cloud model would look like it worked and do nothing.

    Raises:
        ValidationError: For an out-of-range value or an unsupported parameter.
        ModelNotFound: If the reference names no configured provider.
    """
    from velox_ui.errors import ValidationError

    resolved = await state.providers.resolve(model_ref)
    body = await read_struct(request, ModelParamsInput)
    supported = resolved.provider.supported_params
    unsupported = sorted(
        field
        for field in body.__struct_fields__
        if getattr(body, field) is not None and field not in supported
    )
    if unsupported:
        raise ValidationError(
            f"This model's backend does not accept: {', '.join(unsupported)}.",
            fields=[
                {
                    "field": name,
                    "message": "not supported by this backend",
                    "type": "unsupported",
                }
                for name in unsupported
            ],
        )
    saved = await state.model_params.put(principal.user_id, model_ref, body.to_params())
    return json_response(_params_payload(model_ref, saved, supported))


@router.delete(
    "/model-params/{model_ref:path}", status_code=204, summary="Reset parameters for a model"
)
async def delete_model_params(
    model_ref: str, principal: CurrentPrincipal, state: State
) -> Response:
    """Forget the caller's saved parameters for one model."""
    await state.model_params.clear(principal.user_id, model_ref)
    return Response(status_code=204)


def _params_payload(
    model_ref: str, saved: object, supported: frozenset[str]
) -> dict[str, object]:
    """Render saved parameters, omitting unset fields."""
    values = (
        {
            field: value
            for field in saved.__struct_fields__  # type: ignore[attr-defined]
            if (value := getattr(saved, field)) is not None
        }
        if saved is not None
        else {}
    )
    return {"model_ref": model_ref, "params": values, "supported_params": sorted(supported)}
