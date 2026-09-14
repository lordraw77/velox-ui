"""SqliteVecStore against a real in-memory SQLite database (no mocks of the DB)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from velox_ui.rag.store.base import ScoredChunk, vector_table_name
from velox_ui.rag.store.sqlite_vec import SqliteVecStore


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


COLLECTION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


async def test_upsert_and_query_orders_by_similarity(session: AsyncSession) -> None:
    store = SqliteVecStore()
    await store.ensure_collection(session, COLLECTION_ID, 3)
    await store.upsert(
        session,
        COLLECTION_ID,
        [("a", [1.0, 0.0, 0.0]), ("b", [0.0, 1.0, 0.0]), ("c", [0.9, 0.1, 0.0])],
    )
    await session.commit()

    hits = await store.query(session, COLLECTION_ID, [1.0, 0.0, 0.0], k=3)
    assert [h.chunk_id for h in hits] == ["a", "c", "b"]
    assert all(isinstance(h, ScoredChunk) for h in hits)
    assert hits[0].score > hits[-1].score


async def test_delete_chunks_removes_from_results(session: AsyncSession) -> None:
    store = SqliteVecStore()
    await store.ensure_collection(session, COLLECTION_ID, 2)
    await store.upsert(session, COLLECTION_ID, [("a", [1.0, 0.0]), ("b", [0.0, 1.0])])
    await session.commit()

    await store.delete_chunks(session, COLLECTION_ID, ["a"])
    await session.commit()

    hits = await store.query(session, COLLECTION_ID, [1.0, 0.0], k=5)
    assert [h.chunk_id for h in hits] == ["b"]


async def test_delete_collection_drops_the_table(session: AsyncSession) -> None:
    store = SqliteVecStore()
    await store.ensure_collection(session, COLLECTION_ID, 2)
    await session.commit()

    await store.delete_collection(session, COLLECTION_ID)
    await session.commit()

    # The table is gone; ensure_collection recreates it cleanly.
    await store.ensure_collection(session, COLLECTION_ID, 2)
    await session.commit()


def test_vector_table_name_rejects_non_ulid() -> None:
    with pytest.raises(Exception):  # noqa: B017 - ValidationError, any shape
        vector_table_name("'; DROP TABLE chunk; --")
