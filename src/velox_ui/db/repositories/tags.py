"""Tag repository.

Tags are user-scoped labels applied to chats through the ``chat_tag`` join table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import msgspec
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.db.models import Chat, ChatTag, Tag
from velox_ui.ids import new_ulid

__all__ = ["TagRepository", "TagSummary"]


class TagSummary(msgspec.Struct, frozen=True):
    """One tag, as the UI renders it."""

    id: str
    name: str
    color: str | None


def _to_summary(tag: Tag) -> TagSummary:
    return TagSummary(id=tag.id, name=tag.name, color=tag.color)


class TagRepository:
    """Reads and writes :class:`~velox_ui.db.models.Tag` rows and chat attachments.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def create(self, *, user_id: str, name: str, color: str | None = None) -> Tag:
        """Create a tag."""
        tag = Tag(id=new_ulid(), user_id=user_id, name=name, color=color)
        self._session.add(tag)
        return tag

    async def get(self, tag_id: str, *, user_id: str) -> Tag | None:
        """Return a tag owned by ``user_id``, or ``None``."""
        stmt = select(Tag).where(Tag.id == tag_id, Tag.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, *, user_id: str) -> Sequence[TagSummary]:
        """Return every tag owned by the user, alphabetically."""
        stmt = select(Tag).where(Tag.user_id == user_id).order_by(Tag.name)
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(_to_summary(tag) for tag in rows)

    async def update(
        self, tag_id: str, *, user_id: str, name: str | None = None, color: str | None = None
    ) -> bool:
        """Rename a tag and/or change its color. Returns whether a row was updated."""
        values: dict[str, object] = {}
        if name is not None:
            values["name"] = name
        if color is not None:
            values["color"] = color
        if not values:
            return await self.get(tag_id, user_id=user_id) is not None
        result = await self._session.execute(
            update(Tag).where(Tag.id == tag_id, Tag.user_id == user_id).values(**values)
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def delete(self, tag_id: str, *, user_id: str) -> bool:
        """Delete a tag. Attachments cascade via ``chat_tag.tag_id``."""
        result = await self._session.execute(
            delete(Tag).where(Tag.id == tag_id, Tag.user_id == user_id)
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def attach(self, *, chat_id: str, tag_id: str, user_id: str) -> bool:
        """Attach a tag to a chat, both owned by ``user_id``.

        Idempotent: attaching an already-attached tag is a no-op, not a conflict.

        Returns:
            Whether the chat and tag both exist and belong to the caller.
        """
        chat = (
            await self._session.execute(
                select(Chat.id).where(Chat.id == chat_id, Chat.user_id == user_id)
            )
        ).scalar_one_or_none()
        tag = (
            await self._session.execute(
                select(Tag.id).where(Tag.id == tag_id, Tag.user_id == user_id)
            )
        ).scalar_one_or_none()
        if chat is None or tag is None:
            return False
        existing = (
            await self._session.execute(
                select(ChatTag.chat_id).where(
                    ChatTag.chat_id == chat_id, ChatTag.tag_id == tag_id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            self._session.add(ChatTag(chat_id=chat_id, tag_id=tag_id))
            # Autoflush is off for this session (repository convention), so without
            # this a second attach in the same transaction would not see the pending
            # insert and would violate the primary key instead of being a no-op.
            await self._session.flush()
        return True

    async def detach(self, *, chat_id: str, tag_id: str, user_id: str) -> bool:
        """Detach a tag from a chat. Returns whether a row was removed."""
        result = await self._session.execute(
            delete(ChatTag).where(
                ChatTag.chat_id == chat_id,
                ChatTag.tag_id == tag_id,
                ChatTag.chat_id.in_(select(Chat.id).where(Chat.user_id == user_id)),
            )
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def for_chat(self, chat_id: str) -> Sequence[TagSummary]:
        """Return the tags attached to one chat."""
        stmt = (
            select(Tag)
            .join(ChatTag, ChatTag.tag_id == Tag.id)
            .where(ChatTag.chat_id == chat_id)
            .order_by(Tag.name)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(_to_summary(tag) for tag in rows)

    async def for_chats(self, chat_ids: Sequence[str]) -> dict[str, list[TagSummary]]:
        """Return tags for several chats at once, batched to avoid N+1 queries."""
        if not chat_ids:
            return {}
        stmt = (
            select(ChatTag.chat_id, Tag)
            .join(Tag, Tag.id == ChatTag.tag_id)
            .where(ChatTag.chat_id.in_(chat_ids))
            .order_by(Tag.name)
        )
        rows = (await self._session.execute(stmt)).all()
        grouped: dict[str, list[TagSummary]] = {chat_id: [] for chat_id in chat_ids}
        for chat_id, tag in rows:
            grouped.setdefault(chat_id, []).append(_to_summary(tag))
        return grouped

    async def chat_ids_for_tag(self, tag_id: str, *, user_id: str) -> Sequence[str]:
        """Return the ids of chats carrying a given tag, owned by the caller."""
        stmt = (
            select(ChatTag.chat_id)
            .join(Chat, Chat.id == ChatTag.chat_id)
            .where(ChatTag.tag_id == tag_id, Chat.user_id == user_id, Chat.deleted_at.is_(None))
        )
        return tuple((await self._session.execute(stmt)).scalars().all())
