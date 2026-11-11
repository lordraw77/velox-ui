"""folder pinned

Revision ID: b2d7e4f19a63
Revises: 8a4c1d7e5b92
Create date: 2026-11-11 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d7e4f19a63"
down_revision: str | None = "8a4c1d7e5b92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("folder") as batch:
        batch.add_column(
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Revert the migration."""
    with op.batch_alter_table("folder") as batch:
        batch.drop_column("pinned")
