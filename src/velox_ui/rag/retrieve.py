"""Retrieval: embed a query, run vector KNN, return chunks with document context.

Used by both the debug/preview route (``POST /api/collections/{id}/query``) and the
chat wiring that turns a custom model's ``knowledge_ids`` into ``citation`` events
(``services/chat.py``). No BM25/RRF fusion or reranking: ADR-0011 scopes hybrid search
and reranking as follow-ons, not this phase's baseline — vector-only KNN is what the
brief's retrieval endpoint needs to exist and be useful.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import msgspec

from velox_ui.db.repositories.rag import ChunkRepository, DocumentRepository
from velox_ui.rag.embedders import registry as embedder_registry
from velox_ui.rag.store.registry import vector_store_for

if TYPE_CHECKING:
    from velox_ui.state import AppState

__all__ = ["RetrievedChunk", "retrieve"]


class _CollectionLike(Protocol):
    """The subset of ``Collection`` retrieval needs.

    Works with the ORM row, or with the detached
    :class:`~velox_ui.db.repositories.rag.CollectionSummary` a caller fetches after
    its own session has closed.
    """

    @property
    def id(self) -> str: ...

    @property
    def embedder_ref(self) -> str: ...

    @property
    def dim(self) -> int: ...


class RetrievedChunk(msgspec.Struct, frozen=True):
    """One retrieval hit, with enough context to render a citation."""

    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    locator: dict[str, Any] | None


async def retrieve(
    state: AppState, *, collection: _CollectionLike, query: str, k: int = 5
) -> list[RetrievedChunk]:
    """Embed ``query`` and return its ``k`` nearest chunks in ``collection``, best first."""
    embedder = embedder_registry.resolve_embedder(
        collection.embedder_ref, state, dim=collection.dim
    )
    vectors = await embedder.embed([query])
    store = vector_store_for(is_sqlite=state.db.is_sqlite)

    results: list[RetrievedChunk] = []
    async with state.db.session() as session:
        hits = await store.query(session, collection.id, vectors[0], k)
        chunks = await ChunkRepository(session).by_ids([hit.chunk_id for hit in hits])
        document_ids = {chunk.document_id for chunk in chunks.values()}
        titles = await DocumentRepository(session).titles_by_ids(list(document_ids))

        # Built while the session is still open: a Chunk row's attributes are
        # expired on rollback (the read session's cleanup) and raise
        # DetachedInstanceError on first access once the session has closed.
        for hit in hits:
            chunk = chunks.get(hit.chunk_id)
            if chunk is None:
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=hit.chunk_id,
                    document_id=chunk.document_id,
                    document_title=titles.get(chunk.document_id, ""),
                    content=chunk.content,
                    score=hit.score,
                    locator=chunk.locator,
                )
            )
    return results
