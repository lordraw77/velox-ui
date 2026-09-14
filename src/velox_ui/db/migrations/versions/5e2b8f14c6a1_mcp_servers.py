"""mcp servers

Revision ID: 5e2b8f14c6a1
Revises: 3a7c9e21f4d8
Create date: 2026-09-14 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import velox_ui.db.types

revision: str = "5e2b8f14c6a1"
down_revision: str | None = "3a7c9e21f4d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "mcp_server",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("owner_id", sa.String(length=26), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("config", velox_ui.db.types.PackedJson(), nullable=False),
        sa.Column("auth_ref", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("approval", sa.String(length=16), nullable=False),
        sa.Column("tool_cache", velox_ui.db.types.PackedJson(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("mcp_server", schema=None) as batch_op:
        batch_op.create_index("ix_mcp_server_owner", ["owner_id"], unique=False)


def downgrade() -> None:
    """Revert the migration."""
    with op.batch_alter_table("mcp_server", schema=None) as batch_op:
        batch_op.drop_index("ix_mcp_server_owner")
    op.drop_table("mcp_server")
