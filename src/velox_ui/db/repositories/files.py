"""File repository.

An uploaded blob's bytes live under the data directory (``security/uploads.py``); this
repository only tracks the metadata row. ``sha256`` lets a second upload of identical
content by the same user be recognised and reused instead of stored twice.
"""

from __future__ import annotations

import msgspec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import File
from velox_ui.ids import new_ulid

__all__ = ["FileRepository", "FileSummary"]


class FileSummary(msgspec.Struct, frozen=True):
    """A file, as the API reports it."""

    id: str
    user_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: int


def to_summary(file: File) -> FileSummary:
    """Adapt an ORM row into its response struct."""
    return FileSummary(
        id=file.id,
        user_id=file.user_id,
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        created_at=file.created_at,
    )


class FileRepository:
    """Reads and writes :class:`~velox_ui.db.models.File` rows.

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
        user_id: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
    ) -> File:
        """Insert a new file row."""
        file = File(
            id=new_ulid(),
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_key=storage_key,
            created_at=now_ms(),
        )
        self._session.add(file)
        return file

    async def get(self, file_id: str) -> File | None:
        """Return a file by id."""
        return await self._session.get(File, file_id)

    async def by_hash(self, *, user_id: str, sha256: str) -> File | None:
        """Return an existing upload with identical content for the same user, if any."""
        stmt = (
            select(File)
            .where(File.user_id == user_id, File.sha256 == sha256)
            .order_by(File.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def delete(self, file_id: str, *, user_id: str) -> bool:
        """Delete a file owned by ``user_id``. Returns whether a row was removed."""
        file = await self._session.get(File, file_id)
        if file is None or file.user_id != user_id:
            return False
        await self._session.delete(file)
        return True
