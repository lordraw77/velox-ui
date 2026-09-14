"""SQLite vector search via the sqlite-vec loadable extension (ADR-0011, default).

Brute-force KNN over a ``vec0`` virtual table, one per collection, named from
:func:`~.base.vector_table_name`. Cosine distance, since that is what every embedder
here (fastembed, provider-backed) is normalized for.

The extension is loaded once per pooled DBAPI connection, not once per process: SQLite
loadable extensions are a connection-level feature, and the async driver
(``aiosqlite``) hands out one real ``sqlite3.Connection`` per pool slot, cycled across
concurrent sessions.
"""

from __future__ import annotations

import json
import weakref
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.rag.store.base import ScoredChunk, vector_table_name

__all__ = ["SqliteVecStore"]

# Connections for which the extension has already been loaded. A WeakSet, since the
# driver connection object outlives individual sessions but not the pool.
_extension_loaded: weakref.WeakSet[object] = weakref.WeakSet()


async def _ensure_extension_loaded(session: AsyncSession) -> None:
    """Load sqlite-vec into the current connection, once."""
    import sqlite_vec

    conn = await session.connection()
    raw = await conn.get_raw_connection()
    driver = raw.driver_connection
    assert driver is not None  # noqa: S101 - aiosqlite always sets this once connected
    if driver in _extension_loaded:
        return
    await driver.enable_load_extension(True)
    await driver.load_extension(sqlite_vec.loadable_path())
    await driver.enable_load_extension(False)
    _extension_loaded.add(driver)


class SqliteVecStore:
    """Vector search backed by one ``vec0`` table per collection."""

    async def ensure_collection(
        self, session: AsyncSession, collection_id: str, dim: int
    ) -> None:
        """Create the collection's ``vec0`` table if it does not already exist."""
        await _ensure_extension_loaded(session)
        table = vector_table_name(collection_id)
        await session.execute(
            text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0("
                f"chunk_id TEXT PRIMARY KEY, "
                f"embedding float[{int(dim)}] distance_metric=cosine)"
            )
        )

    async def upsert(
        self,
        session: AsyncSession,
        collection_id: str,
        items: Sequence[tuple[str, list[float]]],
    ) -> None:
        """Replace each chunk's vector. ``vec0`` has no native upsert, so delete then insert."""
        if not items:
            return
        await _ensure_extension_loaded(session)
        table = vector_table_name(collection_id)
        chunk_ids = [chunk_id for chunk_id, _ in items]
        await session.execute(
            text(f"DELETE FROM {table} WHERE chunk_id IN :ids").bindparams(
                bindparam("ids", chunk_ids, expanding=True)
            )
        )
        for chunk_id, vector in items:
            await session.execute(
                text(
                    f"INSERT INTO {table}(chunk_id, embedding) VALUES (:chunk_id, :embedding)"
                ),
                {"chunk_id": chunk_id, "embedding": json.dumps(vector)},
            )

    async def delete_chunks(
        self, session: AsyncSession, collection_id: str, chunk_ids: Sequence[str]
    ) -> None:
        """Remove vectors for the given chunk ids."""
        if not chunk_ids:
            return
        await _ensure_extension_loaded(session)
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
        await _ensure_extension_loaded(session)
        table = vector_table_name(collection_id)
        result = await session.execute(
            text(
                f"SELECT chunk_id, distance FROM {table} "
                f"WHERE embedding MATCH :query AND k = :k ORDER BY distance"
            ),
            {"query": json.dumps(query_vector), "k": int(k)},
        )
        return [ScoredChunk(chunk_id=row.chunk_id, score=1.0 - row.distance) for row in result]
