"""The vector store contract.

Two implementations exist, chosen by dialect exactly like ``db/fts/`` splits FTS5 from
tsvector (ADR-0003 pattern, applied here per ADR-0011): :mod:`.sqlite_vec` (default,
brute-force KNN via the sqlite-vec loadable extension) and :mod:`.pgvector`
(HNSW-indexed, PostgreSQL only). Each collection gets its own physical vector table,
named from the collection id, because both sqlite-vec's ``vec0`` and pgvector's
``vector(n)`` column type are fixed-width per table and different collections may use
embedders of different dimensions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.errors import ValidationError

__all__ = ["ScoredChunk", "VectorStore", "vector_table_name"]

_SAFE_ID = re.compile(r"^[0-9A-Z]{26}$")


def vector_table_name(collection_id: str) -> str:
    """Return the physical table name for one collection's vectors.

    Args:
        collection_id: The owning collection's ULID.

    Raises:
        ValidationError: If ``collection_id`` is not a well-formed ULID. Vector table
            names are built by string interpolation (neither dialect parametrizes
            table names), so this is the injection guard for that interpolation, not
            just a niceness check — it must reject anything that is not exactly the
            26-character Crockford-base32 shape ULIDs always have.
    """
    if not _SAFE_ID.match(collection_id):
        raise ValidationError("Malformed collection id.")
    return f"chunk_vec_{collection_id}"


class ScoredChunk:
    """One vector search hit."""

    __slots__ = ("chunk_id", "score")

    def __init__(self, chunk_id: str, score: float) -> None:
        """Store the match."""
        self.chunk_id = chunk_id
        self.score = score


class VectorStore(Protocol):
    """Dialect-neutral vector search over one collection's chunks."""

    async def ensure_collection(
        self, session: AsyncSession, collection_id: str, dim: int
    ) -> None:
        """Create the collection's vector table if it does not already exist."""
        ...

    async def upsert(
        self,
        session: AsyncSession,
        collection_id: str,
        items: Sequence[tuple[str, list[float]]],
    ) -> None:
        """Insert or replace vectors, keyed by chunk id."""
        ...

    async def delete_chunks(
        self, session: AsyncSession, collection_id: str, chunk_ids: Sequence[str]
    ) -> None:
        """Remove vectors for the given chunk ids."""
        ...

    async def delete_collection(self, session: AsyncSession, collection_id: str) -> None:
        """Drop the collection's vector table entirely."""
        ...

    async def query(
        self,
        session: AsyncSession,
        collection_id: str,
        query_vector: list[float],
        k: int,
    ) -> list[ScoredChunk]:
        """Return the ``k`` nearest chunks by cosine distance, closest first."""
        ...
