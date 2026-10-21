"""favorite models

Revision ID: 6b3d9e5a1c72
Revises: 4d8f2b61a7c3
Create date: 2026-10-21 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b3d9e5a1c72"
down_revision: str | None = "4d8f2b61a7c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "favorite_model",
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("model_ref", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "model_ref"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("favorite_model")
