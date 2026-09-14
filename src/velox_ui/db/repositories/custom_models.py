"""Custom model (persona) repository.

A custom model bundles a system prompt, sampling parameter overrides and a fallback
chain under a slug, so a chat can be started from it directly. ``tools`` is carried
for phase 8 (MCP) and still only ever written as ``None``. ``knowledge_ids`` — RAG
collection ids to retrieve from before a turn — is real as of phase 7: the client
resolves it when starting a chat from this custom model and sends it on
``POST .../completions`` the same way it already resolves ``system_prompt``
(``services/chat.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import msgspec
from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import CustomModel
from velox_ui.ids import new_ulid

__all__ = ["CustomModelRepository", "CustomModelSummary", "FallbackEntry", "to_summary"]


class FallbackEntry(msgspec.Struct, frozen=True):
    """One step of a fallback chain."""

    provider_id: str
    model_key: str


class CustomModelSummary(msgspec.Struct, frozen=True):
    """A custom model, as the UI renders it."""

    id: str
    owner_id: str | None
    slug: str
    name: str
    description: str | None
    avatar_url: str | None
    system_prompt: str | None
    params: dict[str, Any] | None
    knowledge_ids: tuple[str, ...]
    fallback_chain: tuple[FallbackEntry, ...]
    visibility: str
    created_at: int
    updated_at: int


def to_summary(model: CustomModel) -> CustomModelSummary:
    """Adapt an ORM row into its response struct."""
    chain = model.fallback_chain or []
    return CustomModelSummary(
        id=model.id,
        owner_id=model.owner_id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        avatar_url=model.avatar_url,
        system_prompt=model.system_prompt,
        params=model.params,
        knowledge_ids=tuple(model.knowledge_ids or ()),
        fallback_chain=tuple(
            FallbackEntry(provider_id=entry["provider_id"], model_key=entry["model_key"])
            for entry in chain
        ),
        visibility=model.visibility,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class CustomModelRepository:
    """Reads and writes :class:`~velox_ui.db.models.CustomModel` rows.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def create(
        self,
        *,
        owner_id: str,
        slug: str,
        name: str,
        description: str | None = None,
        avatar_url: str | None = None,
        system_prompt: str | None = None,
        params: dict[str, Any] | None = None,
        knowledge_ids: Sequence[str] = (),
        fallback_chain: Sequence[FallbackEntry] = (),
        visibility: str = "private",
    ) -> CustomModel:
        """Create a custom model owned by ``owner_id``."""
        moment = now_ms()
        model = CustomModel(
            id=new_ulid(),
            owner_id=owner_id,
            slug=slug,
            name=name,
            description=description,
            avatar_url=avatar_url,
            system_prompt=system_prompt,
            params=params,
            tools=None,
            knowledge_ids=list(knowledge_ids) or None,
            fallback_chain=[msgspec.structs.asdict(entry) for entry in fallback_chain],
            visibility=visibility,
            created_at=moment,
            updated_at=moment,
        )
        self._session.add(model)
        return model

    async def get(self, model_id: str) -> CustomModel | None:
        """Return a custom model by id, regardless of ownership.

        Visibility is enforced by the caller, which knows the requester's identity;
        the repository only fetches.
        """
        return await self._session.get(CustomModel, model_id)

    async def by_slug(self, slug: str) -> CustomModel | None:
        """Return a custom model by its unique slug."""
        stmt = select(CustomModel).where(CustomModel.slug == slug).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_visible(self, *, user_id: str) -> Sequence[CustomModelSummary]:
        """Return the custom models a user may see: their own, plus shared or public ones."""
        stmt = (
            select(CustomModel)
            .where(
                or_(
                    CustomModel.owner_id == user_id,
                    CustomModel.visibility.in_(("shared", "public")),
                )
            )
            .order_by(CustomModel.name)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(to_summary(model) for model in rows)

    async def update(
        self,
        model_id: str,
        *,
        owner_id: str,
        name: str | None = None,
        description: str | None = None,
        avatar_url: str | None = None,
        system_prompt: str | None = None,
        params: dict[str, Any] | None = None,
        knowledge_ids: Sequence[str] | None = None,
        fallback_chain: Sequence[FallbackEntry] | None = None,
        visibility: str | None = None,
    ) -> bool:
        """Update a custom model owned by ``owner_id``. Returns whether it was found."""
        values: dict[str, object] = {"updated_at": now_ms()}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if avatar_url is not None:
            values["avatar_url"] = avatar_url
        if system_prompt is not None:
            values["system_prompt"] = system_prompt
        if params is not None:
            values["params"] = params
        if knowledge_ids is not None:
            values["knowledge_ids"] = list(knowledge_ids) or None
        if fallback_chain is not None:
            values["fallback_chain"] = [
                msgspec.structs.asdict(entry) for entry in fallback_chain
            ]
        if visibility is not None:
            values["visibility"] = visibility
        result = await self._session.execute(
            update(CustomModel)
            .where(CustomModel.id == model_id, CustomModel.owner_id == owner_id)
            .values(**values)
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def delete(self, model_id: str, *, owner_id: str) -> bool:
        """Delete a custom model owned by ``owner_id``."""
        model = await self.get(model_id)
        if model is None or model.owner_id != owner_id:
            return False
        await self._session.delete(model)
        return True
