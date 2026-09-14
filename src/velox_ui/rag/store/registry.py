"""Picks the vector store implementation for the configured database dialect.

A plain function rather than the ``velox_ui.stores`` entry-point group ADR-0014 names
for a future phase — see ``rag/embedders/base.py`` for why entry-point discovery is
deferred until the plugin loader it depends on actually exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from velox_ui.rag.store.base import VectorStore

__all__ = ["vector_store_for"]


def vector_store_for(*, is_sqlite: bool) -> VectorStore:
    """Return the vector store for the database in use."""
    if is_sqlite:
        from velox_ui.rag.store.sqlite_vec import SqliteVecStore

        return SqliteVecStore()
    from velox_ui.rag.store.pgvector import PgVectorStore

    return PgVectorStore()
