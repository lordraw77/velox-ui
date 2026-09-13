"""providers and model params

Revision ID: 7c1e4a9d2b30
Revises: 02331038acbb
Create date: 2026-09-13 09:30:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import velox_ui.db.types

revision: str = "7c1e4a9d2b30"
down_revision: str | None = "02331038acbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "provider",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("preset", sa.String(length=64), nullable=True),
        sa.Column("base_url", sa.String(length=1000), nullable=False),
        sa.Column("auth_ref", sa.String(length=128), nullable=True),
        sa.Column("extra", velox_ui.db.types.PackedJson(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_local", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_params",
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("model_ref", sa.String(length=255), nullable=False),
        sa.Column("params", velox_ui.db.types.PackedJson(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "model_ref"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("model_params")
    op.drop_table("provider")
