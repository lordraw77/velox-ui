"""Collection, document and chunk repositories.

A collection is a knowledge base: a named group of documents sharing one embedder and
vector dimension. Documents are ingested from an uploaded file or a URL and hold their
own ``status``/``progress`` (mirrored from the ingest/embed job, ADR-0019); chunks are
the retrievable units produced by :mod:`velox_ui.rag.chunking`.

Vector rows themselves are not here: they live in the dialect-specific store built by
``rag/store/`` and are written by the ingest job alongside these SQL rows, keyed by
chunk id.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import msgspec
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import Chunk, Collection, Document
from velox_ui.ids import new_ulid
from velox_ui.rag.chunking import ChunkingConfig

__all__ = [
    "ChunkRepository",
    "ChunkRow",
    "CollectionRepository",
    "CollectionSummary",
    "DocumentRepository",
    "DocumentSummary",
    "to_chunk_row",
    "to_collection_summary",
    "to_document_summary",
]


class CollectionSummary(msgspec.Struct, frozen=True):
    """A collection, as the API reports it."""

    id: str
    owner_id: str
    name: str
    description: str | None
    embedder_ref: str
    dim: int
    chunking: dict[str, Any]
    visibility: str
    created_at: int


class DocumentSummary(msgspec.Struct, frozen=True):
    """A document, as the API reports it."""

    id: str
    collection_id: str
    file_id: str | None
    source_url: str | None
    title: str
    status: str
    progress: int
    error: str | None
    chunk_count: int
    created_at: int
    updated_at: int


class ChunkRow(msgspec.Struct, frozen=True):
    """A chunk, for debug/preview retrieval."""

    id: str
    document_id: str
    ordinal: int
    content: str
    token_count: int
    locator: dict[str, Any] | None


def to_collection_summary(collection: Collection) -> CollectionSummary:
    """Adapt an ORM row into its response struct."""
    return CollectionSummary(
        id=collection.id,
        owner_id=collection.owner_id,
        name=collection.name,
        description=collection.description,
        embedder_ref=collection.embedder_ref,
        dim=collection.dim,
        chunking=collection.chunking,
        visibility=collection.visibility,
        created_at=collection.created_at,
    )


def to_document_summary(document: Document) -> DocumentSummary:
    """Adapt an ORM row into its response struct."""
    return DocumentSummary(
        id=document.id,
        collection_id=document.collection_id,
        file_id=document.file_id,
        source_url=document.source_url,
        title=document.title,
        status=document.status,
        progress=document.progress,
        error=document.error,
        chunk_count=document.chunk_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def to_chunk_row(chunk: Chunk) -> ChunkRow:
    """Adapt an ORM row into its response struct."""
    return ChunkRow(
        id=chunk.id,
        document_id=chunk.document_id,
        ordinal=chunk.ordinal,
        content=chunk.content,
        token_count=chunk.token_count,
        locator=chunk.locator,
    )


class CollectionRepository:
    """Reads and writes :class:`~velox_ui.db.models.Collection` rows.

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
        name: str,
        description: str | None,
        embedder_ref: str,
        dim: int,
        chunking: ChunkingConfig,
        visibility: str = "private",
    ) -> Collection:
        """Create a collection.

        Its vector table is created separately by the route, through
        ``rag/store/``, once this row (and its id) exists.
        """
        moment = now_ms()
        collection = Collection(
            id=new_ulid(),
            owner_id=owner_id,
            name=name,
            description=description,
            embedder_ref=embedder_ref,
            dim=dim,
            chunking=msgspec.structs.asdict(chunking),
            visibility=visibility,
            created_at=moment,
        )
        self._session.add(collection)
        return collection

    async def get(self, collection_id: str) -> Collection | None:
        """Return a collection by id, regardless of ownership."""
        return await self._session.get(Collection, collection_id)

    async def list_visible(self, *, user_id: str) -> Sequence[CollectionSummary]:
        """Return the collections a user may see: their own, plus shared or public ones."""
        stmt = (
            select(Collection)
            .where(
                or_(
                    Collection.owner_id == user_id,
                    Collection.visibility.in_(("shared", "public")),
                )
            )
            .order_by(Collection.name)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(to_collection_summary(c) for c in rows)

    async def update(
        self,
        collection_id: str,
        *,
        owner_id: str,
        name: str | None = None,
        description: str | msgspec.UnsetType | None = msgspec.UNSET,
        visibility: str | None = None,
    ) -> Collection | None:
        """Update a collection owned by ``owner_id``. Returns the row, or ``None``."""
        collection = await self._session.get(Collection, collection_id)
        if collection is None or collection.owner_id != owner_id:
            return None
        if name is not None:
            collection.name = name
        if description is not msgspec.UNSET:
            collection.description = description
        if visibility is not None:
            collection.visibility = visibility
        return collection

    async def delete(self, collection_id: str, *, owner_id: str) -> bool:
        """Delete a collection owned by ``owner_id``.

        Its vector table is dropped by the caller, through ``rag/store/``, before or
        after this call.
        """
        collection = await self._session.get(Collection, collection_id)
        if collection is None or collection.owner_id != owner_id:
            return False
        await self._session.delete(collection)
        return True


class DocumentRepository:
    """Reads and writes :class:`~velox_ui.db.models.Document` rows.

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
        collection_id: str,
        file_id: str | None,
        source_url: str | None,
        title: str,
    ) -> Document:
        """Create a document row in ``pending`` status."""
        moment = now_ms()
        document = Document(
            id=new_ulid(),
            collection_id=collection_id,
            file_id=file_id,
            source_url=source_url,
            title=title,
            status="pending",
            progress=0,
            error=None,
            chunk_count=0,
            created_at=moment,
            updated_at=moment,
        )
        self._session.add(document)
        return document

    async def get(self, document_id: str) -> Document | None:
        """Return a document by id."""
        return await self._session.get(Document, document_id)

    async def list_for_collection(self, collection_id: str) -> Sequence[DocumentSummary]:
        """Return every document in a collection, newest first."""
        stmt = (
            select(Document)
            .where(Document.collection_id == collection_id)
            .order_by(Document.created_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(to_document_summary(d) for d in rows)

    async def set_progress(
        self,
        document_id: str,
        *,
        status: str,
        progress: int,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Update a document's ingest status. Read back through its own session."""
        document = await self._session.get(Document, document_id)
        if document is None:
            return
        document.status = status
        document.progress = progress
        document.error = error
        if chunk_count is not None:
            document.chunk_count = chunk_count
        document.updated_at = now_ms()

    async def delete(self, document_id: str, *, collection_id: str) -> bool:
        """Delete a document, scoped to its collection. Returns whether it existed."""
        document = await self._session.get(Document, document_id)
        if document is None or document.collection_id != collection_id:
            return False
        await self._session.delete(document)
        return True

    async def titles_by_ids(self, document_ids: Sequence[str]) -> dict[str, str]:
        """Look up titles for a set of documents, for annotating retrieval hits."""
        if not document_ids:
            return {}
        stmt = select(Document.id, Document.title).where(Document.id.in_(document_ids))
        rows = (await self._session.execute(stmt)).all()
        return {row.id: row.title for row in rows}


class ChunkRepository:
    """Reads and writes :class:`~velox_ui.db.models.Chunk` rows.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def replace_all(
        self, document_id: str, chunks: Sequence[tuple[int, str, int, dict[str, Any] | None]]
    ) -> list[Chunk]:
        """Replace every chunk of a document (re-ingestion re-chunks from scratch).

        Args:
            document_id: The owning document.
            chunks: ``(ordinal, content, token_count, locator)`` tuples, in order.

        Returns:
            The newly created rows, with their ids assigned.
        """
        await self._session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        rows = [
            Chunk(
                id=new_ulid(),
                document_id=document_id,
                ordinal=ordinal,
                content=content,
                token_count=token_count,
                locator=locator,
            )
            for ordinal, content, token_count, locator in chunks
        ]
        self._session.add_all(rows)
        return rows

    async def by_ids(self, chunk_ids: Sequence[str]) -> dict[str, Chunk]:
        """Look up chunks by id, for turning vector-search hits into content."""
        if not chunk_ids:
            return {}
        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.id: row for row in rows}

    async def chunk_ids_for_document(self, document_id: str) -> list[str]:
        """Every chunk id belonging to a document, for cleaning up its vectors."""
        stmt = select(Chunk.id).where(Chunk.document_id == document_id)
        return list((await self._session.execute(stmt)).scalars().all())
