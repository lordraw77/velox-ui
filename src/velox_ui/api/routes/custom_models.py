"""Custom model (persona) CRUD.

Cold path: a custom model is edited occasionally, not on the completion path. Its
system prompt and parameters are applied by the client when starting a chat from it
(``CreateChat.custom_model_id`` on ``POST /api/chats``); this route only manages the
definitions.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.repositories.custom_models import (
    CustomModelRepository,
    CustomModelSummary,
    FallbackEntry,
    to_summary,
)
from velox_ui.errors import ConflictError, ForbiddenError, NotFoundError

router = APIRouter(prefix="/api/custom-models", tags=["custom-models"])

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,98}[a-z0-9])?$")


class FallbackEntryRequest(BaseModel):
    """One step of a fallback chain."""

    provider_id: str = Field(min_length=1, max_length=40)
    model_key: str = Field(min_length=1, max_length=255)


class CreateCustomModelRequest(BaseModel):
    """Payload for creating a custom model."""

    slug: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=1024)
    system_prompt: str | None = None
    params: dict[str, Any] | None = None
    knowledge_ids: list[str] = Field(default_factory=list)
    fallback_chain: list[FallbackEntryRequest] = Field(default_factory=list)
    visibility: str = "private"

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                "the slug must be lowercase letters, digits and hyphens, "
                "not starting or ending with a hyphen"
            )
        return value

    @field_validator("visibility")
    @classmethod
    def _valid_visibility(cls, value: str) -> str:
        if value not in ("private", "shared", "public"):
            raise ValueError("visibility must be 'private', 'shared' or 'public'")
        return value


class UpdateCustomModelRequest(BaseModel):
    """Payload for updating a custom model. Every field is optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=1024)
    system_prompt: str | None = None
    params: dict[str, Any] | None = None
    knowledge_ids: list[str] | None = None
    fallback_chain: list[FallbackEntryRequest] | None = None
    visibility: str | None = None

    @field_validator("visibility")
    @classmethod
    def _valid_visibility(cls, value: str | None) -> str | None:
        if value is not None and value not in ("private", "shared", "public"):
            raise ValueError("visibility must be 'private', 'shared' or 'public'")
        return value


class CustomModelResponse(BaseModel):
    """A custom model, as the UI renders it."""

    id: str
    owner_id: str | None
    slug: str
    name: str
    description: str | None
    avatar_url: str | None
    system_prompt: str | None
    params: dict[str, Any] | None
    knowledge_ids: list[str]
    fallback_chain: list[FallbackEntryRequest]
    visibility: str
    created_at: int
    updated_at: int


def _to_response(model: CustomModelSummary) -> CustomModelResponse:
    """Build a response from a :class:`CustomModelSummary`."""
    return CustomModelResponse(
        id=model.id,
        owner_id=model.owner_id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        avatar_url=model.avatar_url,
        system_prompt=model.system_prompt,
        params=model.params,
        knowledge_ids=list(model.knowledge_ids),
        fallback_chain=[
            FallbackEntryRequest(provider_id=entry.provider_id, model_key=entry.model_key)
            for entry in model.fallback_chain
        ],
        visibility=model.visibility,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


@router.get("", response_model=list[CustomModelResponse], summary="List custom models")
async def list_custom_models(
    principal: CurrentPrincipal, state: State
) -> list[CustomModelResponse]:
    """Return the custom models the caller may see: their own, plus shared or public ones."""
    async with state.db.session() as session:
        models = await CustomModelRepository(session).list_visible(user_id=principal.user_id)
    return [_to_response(model) for model in models]


@router.post(
    "", response_model=CustomModelResponse, status_code=201, summary="Create a custom model"
)
async def create_custom_model(
    payload: CreateCustomModelRequest, principal: CurrentPrincipal, state: State
) -> CustomModelResponse:
    """Create a custom model owned by the caller.

    Raises:
        ConflictError: If the slug is already taken.
    """
    async with state.db.write() as session:
        repository = CustomModelRepository(session)
        if await repository.by_slug(payload.slug) is not None:
            raise ConflictError("A custom model with this slug already exists.")
        model = await repository.create(
            owner_id=principal.user_id,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            avatar_url=payload.avatar_url,
            system_prompt=payload.system_prompt,
            params=payload.params,
            knowledge_ids=payload.knowledge_ids,
            fallback_chain=[
                FallbackEntry(provider_id=entry.provider_id, model_key=entry.model_key)
                for entry in payload.fallback_chain
            ],
            visibility=payload.visibility,
        )
        await session.flush()
        model_id = model.id
        created = await repository.get(model_id)
        assert created is not None  # noqa: S101 - just created in this transaction
        return _to_response(to_summary(created))


@router.get("/{model_id}", response_model=CustomModelResponse, summary="Get a custom model")
async def get_custom_model(
    model_id: str, principal: CurrentPrincipal, state: State
) -> CustomModelResponse:
    """Return one custom model.

    Raises:
        NotFoundError: If it does not exist.
        ForbiddenError: If it is private and belongs to someone else.
    """
    async with state.db.session() as session:
        model = await CustomModelRepository(session).get(model_id)
        if model is None:
            raise NotFoundError("No such custom model.")
        if model.visibility == "private" and model.owner_id != principal.user_id:
            raise ForbiddenError("This custom model is private.")
        return _to_response(to_summary(model))


@router.patch(
    "/{model_id}", response_model=CustomModelResponse, summary="Update a custom model"
)
async def update_custom_model(
    model_id: str, payload: UpdateCustomModelRequest, principal: CurrentPrincipal, state: State
) -> CustomModelResponse:
    """Update a custom model owned by the caller.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    async with state.db.write() as session:
        repository = CustomModelRepository(session)
        chain = (
            [
                FallbackEntry(provider_id=e.provider_id, model_key=e.model_key)
                for e in payload.fallback_chain
            ]
            if payload.fallback_chain is not None
            else None
        )
        updated = await repository.update(
            model_id,
            owner_id=principal.user_id,
            name=payload.name,
            description=payload.description,
            avatar_url=payload.avatar_url,
            system_prompt=payload.system_prompt,
            params=payload.params,
            knowledge_ids=payload.knowledge_ids,
            fallback_chain=chain,
            visibility=payload.visibility,
        )
        if not updated:
            raise NotFoundError("No such custom model.")
        model = await repository.get(model_id)
        assert model is not None  # noqa: S101 - just updated in this transaction
        return _to_response(to_summary(model))


@router.delete("/{model_id}", status_code=204, summary="Delete a custom model")
async def delete_custom_model(model_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Delete a custom model owned by the caller.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    async with state.db.write() as session:
        removed = await CustomModelRepository(session).delete(
            model_id, owner_id=principal.user_id
        )
    if not removed:
        raise NotFoundError("No such custom model.")
