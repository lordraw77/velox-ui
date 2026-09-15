"""Files and RAG: collections, document ingestion, debug retrieval, web search.

Cold path throughout: creating a collection, uploading a document or previewing a
query are deliberate management actions, never part of a completion turn. Ingestion
itself runs as a background job (``services/rag_jobs.py``, ADR-0019), so
``POST .../documents`` returns as soon as the job is queued rather than blocking on
parsing and embedding.
"""

from __future__ import annotations

from typing import Annotated, Any

import msgspec
from fastapi import APIRouter, Form, UploadFile
from fastapi import File as FastApiFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.api.msgspec_io import json_response
from velox_ui.api.sse import SSE_HEADERS
from velox_ui.db.repositories.files import FileRepository
from velox_ui.db.repositories.files import to_summary as file_to_summary
from velox_ui.db.repositories.rag import (
    ChunkRepository,
    CollectionRepository,
    DocumentRepository,
    to_collection_summary,
    to_document_summary,
)
from velox_ui.errors import ForbiddenError, NotFoundError
from velox_ui.rag.chunking import ChunkingConfig
from velox_ui.rag.embedders.registry import dim_for_ref
from velox_ui.security.uploads import read_upload, store_upload, validate_upload
from velox_ui.services.rag_jobs import rag_job_events

router = APIRouter(tags=["rag"])

_VISIBILITIES = ("private", "shared", "public")


class FileResponse(BaseModel):
    """An uploaded file's metadata."""

    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: int


class CollectionResponse(BaseModel):
    """A knowledge base."""

    id: str
    owner_id: str
    name: str
    description: str | None
    embedder_ref: str
    dim: int
    chunking: dict[str, Any]
    visibility: str
    created_at: int


class CreateCollectionRequest(BaseModel):
    """Payload for creating a collection."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    embedder_ref: str = Field(default="fastembed:bge-small-en-v1.5", max_length=200)
    dim: int | None = Field(default=None, gt=0, le=8192)
    max_tokens: int = Field(default=400, gt=0, le=4000)
    overlap_tokens: int = Field(default=60, ge=0, le=2000)
    visibility: str = "private"

    @field_validator("visibility")
    @classmethod
    def _valid_visibility(cls, value: str) -> str:
        if value not in _VISIBILITIES:
            raise ValueError("visibility must be 'private', 'shared' or 'public'")
        return value


class UpdateCollectionRequest(BaseModel):
    """Payload for updating a collection. Every field is optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    visibility: str | None = None

    @field_validator("visibility")
    @classmethod
    def _valid_visibility(cls, value: str | None) -> str | None:
        if value is not None and value not in _VISIBILITIES:
            raise ValueError("visibility must be 'private', 'shared' or 'public'")
        return value


class DocumentResponse(BaseModel):
    """A document within a collection."""

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


class DocumentCreatedResponse(BaseModel):
    """A newly queued document, plus the job following its ingestion."""

    document: DocumentResponse
    job_id: str


class RetrievedChunk(BaseModel):
    """One retrieval hit, for debug/preview queries."""

    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    locator: dict[str, Any] | None


class QueryRequest(BaseModel):
    """A debug/preview retrieval query. No LLM call involved."""

    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, gt=0, le=50)


class QueryResponse(BaseModel):
    """Top-k retrieval hits."""

    items: list[RetrievedChunk]


class WebSearchRequest(BaseModel):
    """A web-search-as-RAG-source request."""

    query: str = Field(min_length=1, max_length=2000)


def _struct_dict(value: msgspec.Struct) -> dict[str, Any]:
    return msgspec.structs.asdict(value)


async def _owned_collection(state: State, collection_id: str, user_id: str) -> Any:
    # Converted to its detached summary struct before the session closes: the read
    # session rolls back on exit, which expires every loaded ORM attribute, so an
    # ORM row handed back after that point raises DetachedInstanceError on first
    # access rather than lazily reloading (there is no session left to reload with).
    async with state.db.session() as session:
        collection = await CollectionRepository(session).get(collection_id)
        if collection is None:
            raise NotFoundError("No such collection.")
        summary = to_collection_summary(collection)
    if summary.owner_id != user_id and summary.visibility == "private":
        raise ForbiddenError("This collection is private.")
    return summary


@router.post(
    "/api/files", response_model=FileResponse, status_code=201, summary="Upload a file"
)
async def upload_file(
    principal: CurrentPrincipal,
    state: State,
    upload: Annotated[UploadFile, FastApiFile()],
) -> FileResponse:
    """Upload a file, sniffed and size-capped, for later use as a RAG document source."""
    data = await upload.read()
    content = validate_upload(upload.filename or "upload", upload.content_type, data)
    storage_key = store_upload(state.settings.data_dir, content)
    async with state.db.write() as session:
        repo = FileRepository(session)
        existing = await repo.by_hash(user_id=principal.user_id, sha256=content.sha256)
        if existing is not None:
            return FileResponse(**_struct_dict(file_to_summary(existing)))
        file = await repo.create(
            user_id=principal.user_id,
            filename=upload.filename or "upload",
            content_type=content.content_type,
            size_bytes=len(content.data),
            sha256=content.sha256,
            storage_key=storage_key,
        )
        await session.flush()
        return FileResponse(**_struct_dict(file_to_summary(file)))


@router.get("/api/files/{file_id}", response_model=FileResponse, summary="Get a file")
async def get_file(file_id: str, principal: CurrentPrincipal, state: State) -> FileResponse:
    """Return one file's metadata.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    async with state.db.session() as session:
        file = await FileRepository(session).get(file_id)
        if file is None or file.user_id != principal.user_id:
            raise NotFoundError("No such file.")
        summary = file_to_summary(file)
    return FileResponse(**_struct_dict(summary))


@router.get("/api/files/{file_id}/content", summary="Get a file's raw bytes")
async def get_file_content(
    file_id: str, principal: CurrentPrincipal, state: State
) -> StreamingResponse:
    """Return a file's bytes, for inline rendering (e.g. a generated image).

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    async with state.db.session() as session:
        file = await FileRepository(session).get(file_id)
        if file is None or file.user_id != principal.user_id:
            raise NotFoundError("No such file.")
        storage_key, content_type = file.storage_key, file.content_type
    data = read_upload(state.settings.data_dir, storage_key)
    return StreamingResponse(iter([data]), media_type=content_type)


@router.delete("/api/files/{file_id}", status_code=204, summary="Delete a file")
async def delete_file(file_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Delete a file owned by the caller."""
    async with state.db.write() as session:
        removed = await FileRepository(session).delete(file_id, user_id=principal.user_id)
    if not removed:
        raise NotFoundError("No such file.")


@router.get(
    "/api/collections", response_model=list[CollectionResponse], summary="List collections"
)
async def list_collections(
    principal: CurrentPrincipal, state: State
) -> list[CollectionResponse]:
    """Return the collections the caller may see: their own, plus shared or public ones."""
    async with state.db.session() as session:
        collections = await CollectionRepository(session).list_visible(
            user_id=principal.user_id
        )
    return [CollectionResponse(**_struct_dict(c)) for c in collections]


@router.post(
    "/api/collections",
    response_model=CollectionResponse,
    status_code=201,
    summary="Create a collection",
)
async def create_collection(
    payload: CreateCollectionRequest, principal: CurrentPrincipal, state: State
) -> CollectionResponse:
    """Create a knowledge base and its vector table.

    Raises:
        ValidationError: If ``embedder_ref`` names an unknown fastembed model, or a
            provider-backed embedder is given with no explicit ``dim``.
    """
    dim = dim_for_ref(payload.embedder_ref, provider_dim=payload.dim)
    async with state.db.write() as session:
        collection = await CollectionRepository(session).create(
            owner_id=principal.user_id,
            name=payload.name,
            description=payload.description,
            embedder_ref=payload.embedder_ref,
            dim=dim,
            chunking=ChunkingConfig(
                max_tokens=payload.max_tokens, overlap_tokens=payload.overlap_tokens
            ),
            visibility=payload.visibility,
        )
        await session.flush()
        collection_id = collection.id

        from velox_ui.rag.store.registry import vector_store_for

        await vector_store_for(is_sqlite=state.db.is_sqlite).ensure_collection(
            session, collection_id, dim
        )
        created = await CollectionRepository(session).get(collection_id)
    assert created is not None  # noqa: S101 - just created in this transaction
    return CollectionResponse(**_struct_dict(to_collection_summary(created)))


@router.get(
    "/api/collections/{collection_id}",
    response_model=CollectionResponse,
    summary="Get a collection",
)
async def get_collection(
    collection_id: str, principal: CurrentPrincipal, state: State
) -> CollectionResponse:
    """Return one collection.

    Raises:
        NotFoundError: If it does not exist.
        ForbiddenError: If it is private and belongs to someone else.
    """
    collection = await _owned_collection(state, collection_id, principal.user_id)
    return CollectionResponse(**_struct_dict(collection))


@router.patch(
    "/api/collections/{collection_id}",
    response_model=CollectionResponse,
    summary="Update a collection",
)
async def update_collection(
    collection_id: str,
    payload: UpdateCollectionRequest,
    principal: CurrentPrincipal,
    state: State,
) -> CollectionResponse:
    """Rename, re-describe or change the visibility of a collection owned by the caller.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    description: str | msgspec.UnsetType | None = msgspec.UNSET
    if "description" in payload.model_fields_set:
        description = payload.description

    async with state.db.write() as session:
        repo = CollectionRepository(session)
        updated = await repo.update(
            collection_id,
            owner_id=principal.user_id,
            name=payload.name,
            description=description,
            visibility=payload.visibility,
        )
        if updated is None:
            raise NotFoundError("No such collection.")
        await session.flush()
        collection = await repo.get(collection_id)
    assert collection is not None  # noqa: S101 - just updated in this transaction
    return CollectionResponse(**_struct_dict(to_collection_summary(collection)))


@router.delete(
    "/api/collections/{collection_id}", status_code=204, summary="Delete a collection"
)
async def delete_collection(
    collection_id: str, principal: CurrentPrincipal, state: State
) -> None:
    """Delete a collection owned by the caller, including its vector table.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    from velox_ui.rag.store.registry import vector_store_for

    async with state.db.write() as session:
        removed = await CollectionRepository(session).delete(
            collection_id, owner_id=principal.user_id
        )
        if not removed:
            raise NotFoundError("No such collection.")
        await vector_store_for(is_sqlite=state.db.is_sqlite).delete_collection(
            session, collection_id
        )


@router.post(
    "/api/collections/{collection_id}/documents",
    response_model=DocumentCreatedResponse,
    status_code=202,
    summary="Ingest a document",
)
async def create_document(
    collection_id: str,
    principal: CurrentPrincipal,
    state: State,
    file_id: Annotated[str, Form()],
    title: Annotated[str | None, Form()] = None,
) -> DocumentCreatedResponse:
    """Queue a document for ingestion from an already-uploaded file.

    Returns as soon as the ingest job is queued, per docs/design/03-http-api.md; the
    document's ``status``/``progress`` move as the job runs (``POST /api/files`` first,
    then this).

    Raises:
        NotFoundError: If the collection or the file does not exist.
        ForbiddenError: If the collection is not the caller's.
    """
    collection = await _owned_collection(state, collection_id, principal.user_id)
    if collection.owner_id != principal.user_id:
        raise ForbiddenError("Only the collection's owner may add documents.")

    async with state.db.write() as session:
        file = await FileRepository(session).get(file_id)
        if file is None or file.user_id != principal.user_id:
            raise NotFoundError("No such file.")
        document = await DocumentRepository(session).create(
            collection_id=collection_id,
            file_id=file_id,
            source_url=None,
            title=title or file.filename,
        )
        await session.flush()
        document_id = document.id

    from velox_ui.services.rag_ingest import run_ingest

    job = state.rag_jobs.start(
        kind="ingest",
        collection_id=collection_id,
        document_id=document_id,
        operation=lambda: run_ingest(
            state, collection_id=collection_id, document_id=document_id
        ),
    )
    async with state.db.session() as session:
        created = await DocumentRepository(session).get(document_id)
        assert created is not None  # noqa: S101 - just created above
        document_response = DocumentResponse(**_struct_dict(to_document_summary(created)))
    return DocumentCreatedResponse(document=document_response, job_id=job.id)


@router.get(
    "/api/collections/{collection_id}/documents",
    response_model=list[DocumentResponse],
    summary="List documents",
)
async def list_documents(
    collection_id: str, principal: CurrentPrincipal, state: State
) -> list[DocumentResponse]:
    """List every document in a collection, with status and progress."""
    await _owned_collection(state, collection_id, principal.user_id)
    async with state.db.session() as session:
        documents = await DocumentRepository(session).list_for_collection(collection_id)
    return [DocumentResponse(**_struct_dict(d)) for d in documents]


@router.delete("/api/documents/{document_id}", status_code=204, summary="Delete a document")
async def delete_document(document_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Delete a document and its chunks and vectors.

    Raises:
        NotFoundError: If it does not exist, or its collection is not the caller's.
    """
    from velox_ui.rag.store.registry import vector_store_for

    async with state.db.session() as session:
        document = await DocumentRepository(session).get(document_id)
        if document is None:
            raise NotFoundError("No such document.")
        collection = await CollectionRepository(session).get(document.collection_id)
        if collection is None or collection.owner_id != principal.user_id:
            raise NotFoundError("No such document.")
        collection_id = document.collection_id

    async with state.db.write() as session:
        chunk_ids = await ChunkRepository(session).chunk_ids_for_document(document_id)
        await vector_store_for(is_sqlite=state.db.is_sqlite).delete_chunks(
            session, collection_id, chunk_ids
        )
        await DocumentRepository(session).delete(document_id, collection_id=collection_id)


@router.post(
    "/api/collections/{collection_id}/query",
    response_model=QueryResponse,
    summary="Preview retrieval",
)
async def query_collection(
    collection_id: str, payload: QueryRequest, principal: CurrentPrincipal, state: State
) -> QueryResponse:
    """Embed a query and return the top-k nearest chunks. No LLM call."""
    collection = await _owned_collection(state, collection_id, principal.user_id)
    from velox_ui.rag.retrieve import retrieve

    hits = await retrieve(state, collection=collection, query=payload.query, k=payload.k)
    return QueryResponse(
        items=[
            RetrievedChunk(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_title=hit.document_title,
                content=hit.content,
                score=hit.score,
                locator=hit.locator,
            )
            for hit in hits
        ]
    )


@router.get("/api/rag-jobs/{job_id}/events", summary="Follow an ingest/embed job")
async def follow_rag_job(job_id: str, principal: CurrentPrincipal, state: State) -> Any:
    """Subscribe to a running or recently finished ingest/embed job.

    Deviates from the single ``GET /api/jobs/{id}`` path named in
    docs/design/03-http-api.md: this codebase keeps one job collection per subsystem
    (``/api/model-jobs`` already does the same for downloads, ADR-0017), so RAG jobs
    get their own prefix instead of a shared job registry that does not otherwise
    exist here.

    Raises:
        NotFoundError: If the job is unknown or has been forgotten.
    """
    del principal
    job = state.rag_jobs.get(job_id)
    if job is None:
        raise NotFoundError("No such job.")
    return StreamingResponse(
        rag_job_events(job), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.post("/api/websearch", status_code=501, summary="Web search as a RAG source")
async def websearch(
    payload: WebSearchRequest, principal: CurrentPrincipal, state: State
) -> Any:
    """Run a configured search provider and return results as ingestable documents.

    No search-provider configuration concept exists anywhere else in this codebase
    (checked ``settings.py`` and every ``ProviderSettings``/preset before writing
    this route), and building one — an API key, a provider choice between something
    like Tavily/Brave/SearXNG, result-to-document mapping — is out of scope for this
    phase's brief. This is therefore a typed stub, not a real integration: it always
    answers ``not_configured`` rather than partially implementing a feature with no
    way to configure it.
    """
    del payload, principal, state
    return json_response(
        {
            "error": {
                "code": "unsupported_capability",
                "message": "No web search provider is configured.",
                "retryable": False,
            }
        },
        status_code=501,
    )
