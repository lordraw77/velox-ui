"""Local model management: what is installed, what is loaded, and changing either.

Every route here asks the adapter what it can do — :class:`ModelInspector` for reading,
:class:`LocalModelAdmin` for changing — and answers ``unsupported_capability`` when it
cannot, so no route knows which backend it is talking to.

Reading is open to every signed-in user. Downloading, deleting, creating and unloading
change a machine other people may be using, so they are for administrators.

Downloads and creations run as server-side jobs (ADR-0017). ``POST .../pull`` starts one
— or joins the one already running for that model — and streams its progress; closing
that stream does not cancel the download.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

import msgspec
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from velox_ui.api.deps import AdminPrincipal, CurrentPrincipal, State
from velox_ui.api.msgspec_io import json_response, read_struct
from velox_ui.api.sse import SSE_HEADERS
from velox_ui.errors import NotFoundError, ValidationError
from velox_ui.providers.base import (
    CreateModelSpec,
    LocalModelAdmin,
    ModelInspector,
    Provider,
)
from velox_ui.providers.errors import UnsupportedCapability
from velox_ui.services.model_jobs import ModelJob, job_events
from velox_ui.state import AppState

router = APIRouter(prefix="/api/providers/{provider_id}/local", tags=["local models"])
jobs_router = APIRouter(prefix="/api/model-jobs", tags=["local models"])

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:]{0,199}$")


class PullRequest(msgspec.Struct, forbid_unknown_fields=True):
    """A model to download."""

    name: str


class CopyRequest(msgspec.Struct, forbid_unknown_fields=True):
    """A model to duplicate under a new name."""

    source: str
    destination: str


class UnloadRequest(msgspec.Struct, forbid_unknown_fields=True):
    """A model to evict from memory."""

    name: str


class CreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    """A model to create, from a Modelfile or from structured fields.

    Attributes:
        name: The new model's name.
        modelfile: Modelfile text. When given, the structured fields must be empty.
        from_model: Installed model to build on.
        system: System prompt to bake in.
        template: Prompt template.
        parameters: Sampling defaults to bake in.
    """

    name: str
    modelfile: str | None = None
    from_model: str | None = None
    system: str | None = None
    template: str | None = None
    parameters: dict[str, Any] = msgspec.field(default_factory=dict)


def _provider(state: AppState, provider_id: str) -> Provider:
    provider = state.providers.get(provider_id)
    if provider is None:
        raise NotFoundError("No such provider.")
    return provider


def _inspector(state: AppState, provider_id: str) -> ModelInspector:
    provider = _provider(state, provider_id)
    if not isinstance(provider, ModelInspector):
        raise UnsupportedCapability(
            "This backend does not report installed or loaded models.", provider_id=provider_id
        )
    return provider


def _admin(state: AppState, provider_id: str) -> LocalModelAdmin:
    provider = _provider(state, provider_id)
    if not isinstance(provider, LocalModelAdmin):
        raise UnsupportedCapability(
            "This backend does not support managing models from velox-ui.",
            provider_id=provider_id,
        )
    return provider


def _checked_name(value: str, field: str = "name") -> str:
    name = value.strip()
    if not _MODEL_NAME.match(name):
        raise ValidationError(
            f"{field} is not a valid model name: letters, digits and . _ - / : only.",
            fields=[{"field": field, "message": "invalid model name", "type": "pattern"}],
        )
    return name


def _job_stream(job: ModelJob, *, detach: bool) -> Response:
    """Answer with the job's progress stream, or just its id when detached."""
    if detach:
        return json_response(msgspec.to_builtins(job.snapshot()), status_code=202)
    return StreamingResponse(
        job_events(job),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Velox-Job": job.id},
    )


@router.get("/models", summary="Installed models with details")
async def installed_models(
    provider_id: str, principal: CurrentPrincipal, state: State
) -> Response:
    """List what is installed on the backend, bypassing the model cache."""
    del principal
    provider = _provider(state, provider_id)
    models = await provider.list_models(refresh=True)
    state.providers.invalidate(provider_id)
    return json_response(
        {
            "models": [
                {
                    "name": model.key,
                    "family": model.family,
                    "parameter_size": model.parameter_size,
                    "quantization": model.capabilities.quantization,
                    "size_bytes": model.capabilities.size_bytes,
                    "context_window": model.capabilities.context_window,
                    "modified_at_ms": model.modified_at_ms,
                }
                for model in models
            ]
        }
    )


@router.get("/models/{name:path}", summary="Details of one installed model")
async def show_model(
    provider_id: str, name: str, principal: CurrentPrincipal, state: State
) -> Response:
    """Describe an installed model: template, baked-in parameters, capabilities."""
    del principal
    details = await _inspector(state, provider_id).show(_checked_name(name))
    return json_response(details)


@router.delete("/models/{name:path}", status_code=204, summary="Delete an installed model")
async def delete_model(
    provider_id: str, name: str, principal: AdminPrincipal, state: State
) -> Response:
    """Remove a model from the backend's storage."""
    del principal
    await _admin(state, provider_id).delete(_checked_name(name))
    state.providers.invalidate(provider_id)
    return Response(status_code=204)


@router.post("/copy", status_code=204, summary="Copy a model under a new name")
async def copy_model(
    provider_id: str, request: Request, principal: AdminPrincipal, state: State
) -> Response:
    """Duplicate an installed model."""
    del principal
    body = await read_struct(request, CopyRequest)
    await _admin(state, provider_id).copy(
        _checked_name(body.source, "source"), _checked_name(body.destination, "destination")
    )
    state.providers.invalidate(provider_id)
    return Response(status_code=204)


@router.post("/pull", summary="Download a model, streaming progress")
async def pull_model(
    provider_id: str,
    request: Request,
    principal: AdminPrincipal,
    state: State,
    detach: Annotated[bool, Query()] = False,
) -> Response:
    """Start or join a download and stream its progress as server-sent events.

    Events: ``progress`` with the job snapshot, then ``done`` with the final one. With
    ``?detach=true`` the job starts and the response is its snapshot instead.
    """
    del principal
    body = await read_struct(request, PullRequest)
    name = _checked_name(body.name)
    admin = _admin(state, provider_id)
    job = state.model_jobs.start(
        kind="pull", provider_id=provider_id, model=name, operation=lambda: admin.pull(name)
    )
    return _job_stream(job, detach=detach)


@router.post("/create", summary="Create a model, streaming progress")
async def create_model(
    provider_id: str,
    request: Request,
    principal: AdminPrincipal,
    state: State,
    detach: Annotated[bool, Query()] = False,
) -> Response:
    """Create a model from a Modelfile or from an installed model plus overrides."""
    del principal
    from velox_ui.providers.modelfile import parse_modelfile

    body = await read_struct(request, CreateRequest)
    name = _checked_name(body.name)
    if body.modelfile is not None:
        if body.from_model or body.system or body.template or body.parameters:
            raise ValidationError("Send either a Modelfile or structured fields, not both.")
        spec = parse_modelfile(body.modelfile, name=name)
    elif body.from_model:
        spec = CreateModelSpec(
            name=name,
            from_model=body.from_model,
            system=body.system or None,
            template=body.template or None,
            parameters=body.parameters,
        )
    else:
        raise ValidationError("Send a Modelfile, or the installed model to build on.")
    _checked_name(spec.from_model, "from")

    admin = _admin(state, provider_id)
    job = state.model_jobs.start(
        kind="create", provider_id=provider_id, model=name, operation=lambda: admin.create(spec)
    )
    return _job_stream(job, detach=detach)


@router.get("/running", summary="Models loaded in memory")
async def running_models(
    provider_id: str, principal: CurrentPrincipal, state: State
) -> Response:
    """List loaded models with their memory use and when they will be unloaded."""
    del principal
    running = await _inspector(state, provider_id).running()
    return json_response({"models": running})


@router.post("/unload", status_code=204, summary="Unload a model from memory")
async def unload_model(
    provider_id: str, request: Request, principal: AdminPrincipal, state: State
) -> Response:
    """Evict a model from memory now instead of when its keep-alive expires."""
    del principal
    body = await read_struct(request, UnloadRequest)
    await _admin(state, provider_id).unload(_checked_name(body.name))
    return Response(status_code=204)


@jobs_router.get("", summary="Model downloads and creations")
async def list_jobs(principal: AdminPrincipal, state: State) -> Response:
    """Running jobs first, then those finished in the last few minutes."""
    del principal
    return json_response({"jobs": state.model_jobs.list()})


@jobs_router.get("/{job_id}/events", summary="Follow a job's progress")
async def follow_job(job_id: str, principal: AdminPrincipal, state: State) -> Response:
    """Subscribe to a job that is already running, e.g. after reloading the page.

    Raises:
        NotFoundError: If the job is unknown or has been forgotten.
    """
    del principal
    job = state.model_jobs.get(job_id)
    if job is None:
        raise NotFoundError("No such job.")
    return _job_stream(job, detach=False)


@jobs_router.delete("/{job_id}", status_code=204, summary="Cancel a job")
async def cancel_job(job_id: str, principal: AdminPrincipal, state: State) -> Response:
    """Stop a running download or creation. Ollama resumes a cancelled pull later.

    Raises:
        NotFoundError: If there is no running job with this id.
    """
    del principal
    if not state.model_jobs.cancel(job_id):
        raise NotFoundError("No running job with this id.")
    return Response(status_code=204)
