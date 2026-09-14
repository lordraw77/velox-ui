"""Folder repository.

Folders organize the chat sidebar into a tree. The schema places no depth limit on
``parent_id`` nesting (docs/design/02-db-schema.md), so this repository does not
either — a caller that wants to cap depth for its UI does so at the route or the
client, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import msgspec
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import Folder
from velox_ui.ids import new_ulid

__all__ = ["FolderRepository", "FolderSummary"]


class FolderSummary(msgspec.Struct, frozen=True):
    """One folder, as the sidebar renders it."""

    id: str
    parent_id: str | None
    name: str
    sort_order: int
    created_at: int


def _to_summary(folder: Folder) -> FolderSummary:
    return FolderSummary(
        id=folder.id,
        parent_id=folder.parent_id,
        name=folder.name,
        sort_order=folder.sort_order,
        created_at=folder.created_at,
    )


class FolderRepository:
    """Reads and writes :class:`~velox_ui.db.models.Folder` rows.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def create(
        self, *, user_id: str, name: str, parent_id: str | None = None, sort_order: int = 0
    ) -> Folder:
        """Create a folder, optionally nested under another one owned by the user."""
        folder = Folder(
            id=new_ulid(),
            user_id=user_id,
            parent_id=parent_id,
            name=name,
            sort_order=sort_order,
            created_at=now_ms(),
        )
        self._session.add(folder)
        return folder

    async def get(self, folder_id: str, *, user_id: str) -> Folder | None:
        """Return a folder owned by ``user_id``, or ``None``."""
        stmt = select(Folder).where(Folder.id == folder_id, Folder.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, *, user_id: str) -> Sequence[FolderSummary]:
        """Return every folder owned by the user, flat.

        The client assembles the tree from ``parent_id``: with the small number of
        folders a person keeps, that is simpler than a recursive query and lets the
        UI render partial trees (e.g. while a folder is being renamed) without a
        second round trip.
        """
        stmt = (
            select(Folder)
            .where(Folder.user_id == user_id)
            .order_by(Folder.parent_id.is_(None).desc(), Folder.sort_order, Folder.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(_to_summary(folder) for folder in rows)

    async def rename(self, folder_id: str, *, user_id: str, name: str) -> bool:
        """Rename a folder. Returns whether a row was updated."""
        result = await self._session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.user_id == user_id)
            .values(name=name)
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def move(
        self, folder_id: str, *, user_id: str, parent_id: str | None, sort_order: int | None
    ) -> bool:
        """Reparent and/or reorder a folder. Returns whether a row was updated."""
        values: dict[str, object] = {"parent_id": parent_id}
        if sort_order is not None:
            values["sort_order"] = sort_order
        result = await self._session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.user_id == user_id)
            .values(**values)
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def delete(self, folder_id: str, *, user_id: str) -> bool:
        """Delete a folder.

        Child folders cascade (``folder.parent_id`` is ``ON DELETE CASCADE``) and
        chats inside it are detached rather than deleted (``chat.folder_id`` is
        ``ON DELETE SET NULL``), both enforced at the database.
        """
        folder = await self.get(folder_id, user_id=user_id)
        if folder is None:
            return False
        await self._session.delete(folder)
        return True
