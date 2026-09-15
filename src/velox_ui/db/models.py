"""ORM models.

Identity, sessions, credentials storage and the conversation tree arrived in phase 1;
interface-configured providers and per-model parameters in phase 4. RAG, MCP and usage
tables arrive with the phases that use them; the schema document
(docs/design/02-db-schema.md) describes the full target.

Conventions:

* Every table uses a ULID primary key and integer epoch-millisecond timestamps.
* ``ondelete="CASCADE"`` is declared on the database, not emulated in Python, so a
  user deletion is one statement rather than a graph walk.
* Indexes exist for the queries the design commits to, and the list index for chats is
  partial so that soft-deleted rows never enter it (ADR-0007).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from velox_ui.db.types import BoolInt, Json, Timestamp, UlidPk, UlidRef, longtext, shortstr

__all__ = [
    "AccessRule",
    "ApiKey",
    "AppUser",
    "Base",
    "Chat",
    "ChatTag",
    "Chunk",
    "Collection",
    "CustomModel",
    "Document",
    "File",
    "Folder",
    "McpServer",
    "Message",
    "ModelParams",
    "OidcIdentity",
    "Provider",
    "RefreshToken",
    "Secret",
    "Setting",
    "Tag",
    "UserQuota",
]


class Base(DeclarativeBase):
    """Declarative base for every model."""


class AppUser(Base):
    """An account. Named ``app_user`` because ``user`` is reserved in PostgreSQL."""

    __tablename__ = "app_user"

    id: Mapped[UlidPk]
    email: Mapped[str] = shortstr(320)
    email_norm: Mapped[str] = shortstr(320)
    name: Mapped[str] = shortstr(200)
    password_hash: Mapped[str | None] = shortstr(255)
    role: Mapped[str] = shortstr(16)
    status: Mapped[str] = shortstr(16)
    avatar_url: Mapped[str | None] = shortstr(1024)
    settings: Mapped[Json | None]
    created_at: Mapped[Timestamp]
    last_seen_at: Mapped[Timestamp | None]

    __table_args__ = (UniqueConstraint("email_norm", name="uq_app_user_email_norm"),)


class OidcIdentity(Base):
    """A federated identity bound to a local account."""

    __tablename__ = "oidc_identity"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    issuer: Mapped[str] = shortstr(512)
    subject: Mapped[str] = shortstr(255)
    created_at: Mapped[Timestamp]

    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_oidc_identity"),)


class RefreshToken(Base):
    """A refresh token, stored only as a hash.

    Tokens rotate on every use and carry a ``family_id``. Presenting a token that was
    already rotated means it leaked, so the whole family is revoked rather than the
    single token.
    """

    __tablename__ = "refresh_token"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = shortstr(64)
    family_id: Mapped[UlidRef]
    expires_at: Mapped[Timestamp]
    revoked_at: Mapped[Timestamp | None]
    user_agent: Mapped[str | None] = shortstr(512)
    created_at: Mapped[Timestamp]

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_token_hash"),
        Index("ix_refresh_user", "user_id", "expires_at"),
        Index("ix_refresh_family", "family_id"),
    )


class ApiKey(Base):
    """A long-lived programmatic credential, stored only as a hash."""

    __tablename__ = "api_key"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = shortstr(200)
    prefix: Mapped[str] = shortstr(16)
    key_hash: Mapped[str] = shortstr(64)
    scopes: Mapped[Json | None]
    last_used_at: Mapped[Timestamp | None]
    expires_at: Mapped[Timestamp | None]
    created_at: Mapped[Timestamp]

    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_key_hash"),
        Index("ix_apikey_user", "user_id"),
    )


class UserQuota(Base):
    """Per-user limits. A missing row means the global configuration applies."""

    __tablename__ = "user_quota"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    requests_per_min: Mapped[int | None] = mapped_column(Integer)
    tokens_per_day: Mapped[int | None] = mapped_column(Integer)
    cost_cents_per_day: Mapped[int | None] = mapped_column(Integer)


class AccessRule(Base):
    """Who may use which provider or model.

    Absence of any rule means "allowed". A self-hosted instance pointing at a local
    Ollama should not need an administrator to grant access to it first.
    """

    __tablename__ = "access_rule"

    id: Mapped[UlidPk]
    subject_kind: Mapped[str] = shortstr(16)
    subject_id: Mapped[str] = shortstr(64)
    object_kind: Mapped[str] = shortstr(16)
    object_id: Mapped[str] = shortstr(255)
    effect: Mapped[str] = shortstr(8)

    __table_args__ = (
        UniqueConstraint(
            "subject_kind", "subject_id", "object_kind", "object_id", name="uq_access_rule"
        ),
    )


class Secret(Base):
    """A credential encrypted at rest (ADR-0013).

    The plaintext never leaves :mod:`velox_ui.security.crypto`; the API exposes only
    ``hint``, a masked form such as ``sk-...4f2a``.
    """

    __tablename__ = "secret"

    ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    nonce: Mapped[bytes] = mapped_column()
    ciphertext: Mapped[bytes] = mapped_column()
    hint: Mapped[str] = shortstr(64)
    updated_at: Mapped[Timestamp]


class Provider(Base):
    """An inference backend configured from the interface.

    Backends named in configuration or found by autodiscovery are not stored here: they
    are rebuilt from their source at every start, so editing ``velox.toml`` never leaves
    a stale copy behind. Only what a person added through the interface is persisted.

    The id is a readable slug rather than a ULID because it is the first half of every
    ``model_ref`` ("lmstudio:qwen2.5-7b"), which appears in the interface, in exports
    and in API calls from other clients.
    """

    __tablename__ = "provider"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = shortstr(200)
    kind: Mapped[str] = shortstr(32)
    preset: Mapped[str | None] = shortstr(64)
    base_url: Mapped[str] = shortstr(1000)
    # Points at a `secret` row; NULL means the backend is used without a credential.
    auth_ref: Mapped[str | None] = shortstr(128)
    extra: Mapped[Json | None]
    enabled: Mapped[BoolInt] = mapped_column(default=True)
    is_local: Mapped[BoolInt] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Timestamp]
    updated_at: Mapped[Timestamp]


class ModelParams(Base):
    """A user's saved sampling parameters for one model.

    Keyed by ``model_ref`` rather than by a foreign key to a provider, because the
    provider may come from configuration and have no row at all. A reference to a model
    that no longer exists is harmless: nothing reads it until that model is used again.
    """

    __tablename__ = "model_params"

    user_id: Mapped[UlidRef] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    model_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    params: Mapped[Json]
    updated_at: Mapped[Timestamp]


class Setting(Base):
    """Runtime settings an administrator can change without restarting."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Json]
    updated_at: Mapped[Timestamp]


class CustomModel(Base):
    """A user-defined model preset (persona).

    Binds a system prompt and parameters to a slug, so a chat can be started from it
    directly.

    ``knowledge_ids`` (RAG collections, phase 7), ``tools`` (MCP server ids, phase 8)
    and ``plugins`` (enabled plugin kinds, e.g. ``["images", "voice"]``, phase 10) are
    all acted on as of their respective phases; ``fallback_chain`` is still carried but
    unused.
    """

    __tablename__ = "custom_model"

    id: Mapped[UlidPk]
    owner_id: Mapped[UlidRef | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE")
    )
    slug: Mapped[str] = shortstr(200)
    name: Mapped[str] = shortstr(200)
    description: Mapped[str | None] = longtext()
    avatar_url: Mapped[str | None] = shortstr(1024)
    system_prompt: Mapped[str | None] = longtext()
    params: Mapped[Json | None]
    tools: Mapped[Json | None]
    knowledge_ids: Mapped[Json | None]
    plugins: Mapped[Json | None]
    fallback_chain: Mapped[Json]
    visibility: Mapped[str] = shortstr(16)
    created_at: Mapped[Timestamp]
    updated_at: Mapped[Timestamp]

    __table_args__ = (UniqueConstraint("slug", name="uq_custom_model_slug"),)


class Folder(Base):
    """A folder in the chat sidebar. Folders nest."""

    __tablename__ = "folder"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("folder.id", ondelete="CASCADE"))
    name: Mapped[str] = shortstr(200)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Timestamp]

    __table_args__ = (Index("ix_folder_user", "user_id", "parent_id"),)


class Chat(Base):
    """A conversation.

    ``active_leaf_id`` is the tip of the branch currently displayed. It deliberately
    has no foreign key: the chat row is updated in the same flush as the message that
    it points at, and a constraint would force an ordering that buys nothing.
    """

    __tablename__ = "chat"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("folder.id", ondelete="SET NULL"))
    title: Mapped[str] = shortstr(500)
    active_leaf_id: Mapped[UlidRef | None]
    model_ref: Mapped[str | None] = shortstr(255)
    custom_model_id: Mapped[str | None] = mapped_column(
        ForeignKey("custom_model.id", ondelete="SET NULL")
    )
    pinned: Mapped[BoolInt] = mapped_column(default=False)
    archived: Mapped[BoolInt] = mapped_column(default=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[Json | None]
    created_at: Mapped[Timestamp]
    updated_at: Mapped[Timestamp]
    deleted_at: Mapped[Timestamp | None]

    __table_args__ = (
        # The one index the 10 000-chat listing depends on. Partial, so soft-deleted
        # rows are absent from the structure rather than filtered out of it.
        Index(
            "ix_chat_list",
            "user_id",
            "archived",
            text("pinned DESC"),
            text("updated_at DESC"),
            text("id DESC"),
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_chat_folder", "user_id", "folder_id"),
    )


class Message(Base):
    """One node of a conversation tree (ADR-0006).

    ``depth`` is denormalized so the active path can be read with a single indexed
    range scan instead of a recursive query; it is assigned once, at insert.
    """

    __tablename__ = "message"

    id: Mapped[UlidPk]
    chat_id: Mapped[UlidRef] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    parent_id: Mapped[UlidRef | None]
    role: Mapped[str] = shortstr(16)
    content: Mapped[str] = longtext()
    reasoning: Mapped[str | None] = longtext()
    status: Mapped[str] = shortstr(16)
    model_ref: Mapped[str | None] = shortstr(255)
    depth: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_micros: Mapped[int | None] = mapped_column(Integer)
    timings: Mapped[Json | None]
    error: Mapped[Json | None]
    meta: Mapped[Json | None]
    created_at: Mapped[Timestamp]

    __table_args__ = (
        Index("ix_message_chat", "chat_id", "depth", "id"),
        Index("ix_message_parent", "chat_id", "parent_id"),
    )


class Tag(Base):
    """A user-defined label applied to chats."""

    __tablename__ = "tag"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = shortstr(100)
    color: Mapped[str | None] = shortstr(16)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tag_user_name"),)


class ChatTag(Base):
    """Association between a chat and a tag."""

    __tablename__ = "chat_tag"

    chat_id: Mapped[str] = mapped_column(
        ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_chat_tag_tag", "tag_id", "chat_id"),)


class File(Base):
    """An uploaded blob: a RAG source document, or (in future phases) a chat attachment.

    The bytes live under the data directory at ``storage_key``; only the metadata is
    here. ``sha256`` lets a second upload of the same content be recognised instead of
    stored twice.
    """

    __tablename__ = "file"

    id: Mapped[UlidPk]
    user_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    filename: Mapped[str] = shortstr(500)
    content_type: Mapped[str] = shortstr(200)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = shortstr(64)
    storage_key: Mapped[str] = shortstr(500)
    created_at: Mapped[Timestamp]

    __table_args__ = (Index("ix_file_user_hash", "user_id", "sha256"),)


class Collection(Base):
    """A knowledge base: a named group of documents sharing one embedder and dimension.

    ``embedder_ref`` and ``dim`` are fixed at creation because every chunk in the
    collection is embedded into the same vector space; changing either would orphan
    the existing vectors, so the API requires a new collection instead.
    """

    __tablename__ = "collection"

    id: Mapped[UlidPk]
    owner_id: Mapped[UlidRef] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = shortstr(200)
    description: Mapped[str | None] = longtext()
    embedder_ref: Mapped[str] = shortstr(200)
    dim: Mapped[int] = mapped_column(Integer)
    chunking: Mapped[Json]
    visibility: Mapped[str] = shortstr(16)
    created_at: Mapped[Timestamp]

    __table_args__ = (Index("ix_collection_owner", "owner_id"),)


class Document(Base):
    """One ingested source (an uploaded file, or a web-search result) within a collection."""

    __tablename__ = "document"

    id: Mapped[UlidPk]
    collection_id: Mapped[UlidRef] = mapped_column(
        ForeignKey("collection.id", ondelete="CASCADE")
    )
    file_id: Mapped[str | None] = mapped_column(ForeignKey("file.id", ondelete="SET NULL"))
    source_url: Mapped[str | None] = shortstr(2000)
    title: Mapped[str] = shortstr(500)
    status: Mapped[str] = shortstr(16)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = longtext()
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Timestamp]
    updated_at: Mapped[Timestamp]

    __table_args__ = (Index("ix_document_collection", "collection_id", "status"),)


class Chunk(Base):
    """One retrievable unit of a document's text (rag/chunking.py).

    The embedding vector is not a column here: it lives in the dialect-specific store
    created by ``rag/store/`` (``chunk_vec`` on SQLite, ``chunk_embedding`` on
    PostgreSQL), keyed by this row's id.
    """

    __tablename__ = "chunk"

    id: Mapped[UlidPk]
    document_id: Mapped[UlidRef] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = longtext()
    token_count: Mapped[int] = mapped_column(Integer)
    locator: Mapped[Json | None]

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),
    )


class McpServer(Base):
    """A configured MCP server (phase 8).

    ``config`` holds the transport's non-secret connection details (command/args/env
    for ``stdio``, url/headers for ``http_sse``); any bearer token or secret env value
    is pulled out into ``secret`` and referenced by ``auth_ref``, never stored here in
    the clear (ADR-0013). ``tool_cache`` is the last successful ``list_tools`` result,
    refreshed by ``POST /api/mcp/servers/{id}/connect`` so a chat turn can offer tools
    without reconnecting on every request.
    """

    __tablename__ = "mcp_server"

    id: Mapped[UlidPk]
    owner_id: Mapped[UlidRef | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE")
    )
    name: Mapped[str] = shortstr(200)
    transport: Mapped[str] = shortstr(16)
    config: Mapped[Json]
    auth_ref: Mapped[str | None] = shortstr(128)
    enabled: Mapped[BoolInt] = mapped_column(default=True)
    approval: Mapped[str] = shortstr(16)
    tool_cache: Mapped[Json | None]
    created_at: Mapped[Timestamp]

    __table_args__ = (Index("ix_mcp_server_owner", "owner_id"),)


def table_names() -> tuple[str, ...]:
    """Return every mapped table name, used by diagnostics and tests."""
    return tuple(Base.metadata.tables)
