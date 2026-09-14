"""Runs one document through parsing, chunking and embedding.

Driven by :class:`~velox_ui.services.rag_jobs.RagJobs` as an ``ingest`` job. Each step
commits the ``document`` row's ``status``/``progress`` on its own, so a client polling
``GET /api/collections/{id}/documents`` sees live state even without following the job
stream — the job stream only adds push updates on top of that durable state.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.db.models import Collection, Document
from velox_ui.db.repositories.files import FileRepository
from velox_ui.db.repositories.rag import (
    ChunkRepository,
    CollectionRepository,
    DocumentRepository,
)
from velox_ui.errors import NotFoundError, ValidationError
from velox_ui.rag.chunking import ChunkingConfig, chunk_text
from velox_ui.rag.embedders import registry as embedder_registry
from velox_ui.rag.store.registry import vector_store_for
from velox_ui.security.uploads import read_upload
from velox_ui.services.rag_jobs import RagProgress
from velox_ui.state import AppState

__all__ = ["extract_text", "run_ingest"]

_log = logging.getLogger("velox.rag_ingest")

_EMBED_BATCH = 32


def extract_text(content_type: str, data: bytes) -> str:
    """Extract plain text from an uploaded file's bytes.

    Raises:
        ValidationError: If the content type has no extractor.
    """
    if content_type in ("text/plain", "text/markdown"):
        return data.decode("utf-8", errors="replace")
    if content_type == "application/pdf":
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    raise ValidationError(f"No text extractor for content type {content_type!r}.")


async def run_ingest(
    state: AppState, *, collection_id: str, document_id: str
) -> AsyncIterator[RagProgress]:
    """Parse, chunk and embed one document, yielding progress along the way.

    Raises:
        NotFoundError: If the collection or document has been deleted meanwhile.
        ValidationError: If the source has no supported text extractor.
    """
    async with state.db.write() as session:
        collection = await CollectionRepository(session).get(collection_id)
        document = await DocumentRepository(session).get(document_id)
        if collection is None or document is None or document.collection_id != collection_id:
            raise NotFoundError("No such document.")
        text = await _load_text(state, session, collection, document)
        await DocumentRepository(session).set_progress(
            document_id, status="parsing", progress=10
        )
    yield RagProgress(status="parsing", progress=10)

    config = ChunkingConfig(**collection.chunking) if collection.chunking else ChunkingConfig()
    chunks = chunk_text(text, config)
    async with state.db.write() as session:
        await DocumentRepository(session).set_progress(
            document_id, status="embedding", progress=30, chunk_count=len(chunks)
        )
    yield RagProgress(status="embedding", progress=30)

    if not chunks:
        async with state.db.write() as session:
            await ChunkRepository(session).replace_all(document_id, [])
            await DocumentRepository(session).set_progress(
                document_id, status="ready", progress=100, chunk_count=0
            )
        yield RagProgress(status="ready", progress=100)
        return

    embedder = embedder_registry.resolve_embedder(
        collection.embedder_ref, state, dim=collection.dim
    )
    store = vector_store_for(is_sqlite=state.db.is_sqlite)

    async with state.db.write() as session:
        rows = await ChunkRepository(session).replace_all(
            document_id, [(c.ordinal, c.content, c.token_count, None) for c in chunks]
        )
        await session.flush()
        chunk_ids = [row.id for row in rows]

    total = len(chunk_ids)
    async with state.db.write() as session:
        await store.ensure_collection(session, collection_id, collection.dim)
    for start in range(0, total, _EMBED_BATCH):
        batch_ids = chunk_ids[start : start + _EMBED_BATCH]
        batch_texts = [c.content for c in chunks[start : start + _EMBED_BATCH]]
        vectors = await embedder.embed(batch_texts)
        async with state.db.write() as session:
            await store.upsert(
                session, collection_id, list(zip(batch_ids, vectors, strict=True))
            )
        done = min(start + _EMBED_BATCH, total)
        progress = 30 + int(done / total * 65)
        async with state.db.write() as session:
            await DocumentRepository(session).set_progress(
                document_id, status="embedding", progress=progress, chunk_count=total
            )
        yield RagProgress(status="embedding", progress=progress)

    async with state.db.write() as session:
        await DocumentRepository(session).set_progress(
            document_id, status="ready", progress=100, chunk_count=total
        )
    yield RagProgress(status="ready", progress=100)


async def _load_text(
    state: AppState, session: AsyncSession, collection: Collection, document: Document
) -> str:
    """Fetch the document's source text: from its file, or raise for a URL source."""
    del collection
    if document.file_id is not None:
        file = await FileRepository(session).get(document.file_id)
        if file is None:
            raise NotFoundError("The document's source file no longer exists.")
        data = read_upload(state.settings.data_dir, file.storage_key)
        return extract_text(file.content_type, data)
    if document.source_url is not None:
        # Fetching and rendering a URL's body is out of scope for this phase's ingest
        # path; `/api/websearch` (services/websearch.py) is the documented entry point
        # for URL-sourced documents and writes the extracted text directly.
        raise ValidationError(
            "URL documents must be ingested through /api/websearch, which supplies "
            "extracted text directly."
        )
    raise ValidationError("Document has neither a file nor a source URL.")
