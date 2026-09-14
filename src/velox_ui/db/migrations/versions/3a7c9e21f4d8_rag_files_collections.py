"""rag: files, collections, documents, chunks

Revision ID: 3a7c9e21f4d8
Revises: 9f1c6a2d7b4e
Create date: 2026-09-14 12:00:00.000000+00:00

The per-collection vector table (``chunk_vec`` on SQLite via sqlite-vec, or
``chunk_embedding`` on PostgreSQL via pgvector) is not created here: its column width
depends on the embedder's dimension, chosen when a collection is created, not at
migration time. ``rag/store/`` issues that DDL from the collection repository
(ADR-0019).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import velox_ui.db.types

revision: str = "3a7c9e21f4d8"
down_revision: str | None = "9f1c6a2d7b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "file",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("file", schema=None) as batch_op:
        batch_op.create_index("ix_file_user_hash", ["user_id", "sha256"], unique=False)

    op.create_table(
        "collection",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("owner_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("embedder_ref", sa.String(length=200), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("chunking", velox_ui.db.types.PackedJson(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("collection", schema=None) as batch_op:
        batch_op.create_index("ix_collection_owner", ["owner_id"], unique=False)

    op.create_table(
        "document",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("collection_id", sa.String(length=26), nullable=False),
        sa.Column("file_id", sa.String(length=26), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collection.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["file.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.create_index(
            "ix_document_collection", ["collection_id", "status"], unique=False
        )

    op.create_table(
        "chunk",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("document_id", sa.String(length=26), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("locator", velox_ui.db.types.PackedJson(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("chunk")
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_index("ix_document_collection")
    op.drop_table("document")
    with op.batch_alter_table("collection", schema=None) as batch_op:
        batch_op.drop_index("ix_collection_owner")
    op.drop_table("collection")
    with op.batch_alter_table("file", schema=None) as batch_op:
        batch_op.drop_index("ix_file_user_hash")
    op.drop_table("file")
