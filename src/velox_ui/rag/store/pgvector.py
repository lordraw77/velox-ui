"""PostgreSQL vector search via pgvector, HNSW-indexed (ADR-0011).

Mirrors :mod:`.sqlite_vec`'s per-collection-table shape. No extra Python dependency:
a vector is passed as its pgvector text literal (``'[0.1,0.2,...]'``) and cast with
``::vector`` server-side, since only insert and cosine-distance queries are needed
here — not enough surface to justify the ``pgvector`` package's numpy adapters.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.rag.store.base import ScoredChunk, vector_table_name

__all__ = ["PgVectorStore"]


class PgVectorStore:
    """Vector search backed by one pgvector-indexed table per collection."""

    async def ensure_collection(
        self, session: AsyncSession, collection_id: str, dim: int
    ) -> None:
        """Create the extension (if needed), the collection's table and its HNSW index."""
        await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        table = vector_table_name(collection_id)
        await session.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {table} "
                f"(chunk_id TEXT PRIMARY KEY, embedding vector({int(dim)}))"
            )
        )
        await session.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {table}_hnsw ON {table} "
                f"USING hnsw (embedding vector_cosine_ops)"
            )
        )

    async def upsert(
        self,
        session: AsyncSession,
        collection_id: str,
        items: Sequence[tuple[str, list[float]]],
    ) -> None:
        """Insert or replace vectors, keyed by chunk id."""
        if not items:
            return
        table = vector_table_name(collection_id)
        for chunk_id, vector in items:
            await session.execute(
                text(
                    f"INSERT INTO {table}(chunk_id, embedding) "
                    f"VALUES (:chunk_id, CAST(:embedding AS vector)) "
                    f"ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding"
                ),
                {"chunk_id": chunk_id, "embedding": _literal(vector)},
            )

    async def delete_chunks(
        self, session: AsyncSession, collection_id: str, chunk_ids: Sequence[str]
    ) -> None:
        """Remove vectors for the given chunk ids."""
        if not chunk_ids:
            return
        table = vector_table_name(collection_id)
        await session.execute(
            text(f"DELETE FROM {table} WHERE chunk_id IN :ids").bindparams(
                bindparam("ids", list(chunk_ids), expanding=True)
            )
        )

    async def delete_collection(self, session: AsyncSession, collection_id: str) -> None:
        """Drop the collection's vector table."""
        table = vector_table_name(collection_id)
        await session.execute(text(f"DROP TABLE IF EXISTS {table}"))

    async def query(
        self,
        session: AsyncSession,
        collection_id: str,
        query_vector: list[float],
        k: int,
    ) -> list[ScoredChunk]:
        """Return the ``k`` nearest chunks by cosine distance, closest first."""
        table = vector_table_name(collection_id)
        result = await session.execute(
            text(
                f"SELECT chunk_id, 1 - (embedding <=> :query) AS score FROM {table} "
                f"ORDER BY embedding <=> :query LIMIT :k"
            ),
            {"query": _literal(query_vector), "k": int(k)},
        )
        return [ScoredChunk(chunk_id=row.chunk_id, score=row.score) for row in result]


def _literal(vector: list[float]) -> str:
    """Render a vector as pgvector's text input format."""
    return json.dumps(vector)
