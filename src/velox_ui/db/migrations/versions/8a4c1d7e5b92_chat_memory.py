"""chat memory

Revision ID: 8a4c1d7e5b92
Revises: 6b3d9e5a1c72
Create date: 2026-11-03 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a4c1d7e5b92"
down_revision: str | None = "6b3d9e5a1c72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("chat") as batch:
        batch.add_column(sa.Column("memory", sa.Text(), nullable=True))
        batch.add_column(sa.Column("memory_upto_id", sa.String(length=26), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    with op.batch_alter_table("chat") as batch:
        batch.drop_column("memory_upto_id")
        batch.drop_column("memory")
