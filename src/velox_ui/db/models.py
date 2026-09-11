"""ORM models.

Phase 1 covers identity, sessions, credentials storage and the conversation tree.
Provider, RAG, MCP and usage tables arrive with the phases that use them; the schema
document (docs/design/02-db-schema.md) describes the full target.

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
    "Folder",
    "Message",
    "OidcIdentity",
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


class Setting(Base):
    """Runtime settings an administrator can change without restarting."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Json]
    updated_at: Mapped[Timestamp]


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


def table_names() -> tuple[str, ...]:
    """Return every mapped table name, used by diagnostics and tests."""
    return tuple(Base.metadata.tables)
