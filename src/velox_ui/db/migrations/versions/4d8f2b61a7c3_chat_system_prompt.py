"""chat system prompt

Revision ID: 4d8f2b61a7c3
Revises: 1a2b3c4d5e6f
Create date: 2026-10-08 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d8f2b61a7c3"
down_revision: str | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("chat") as batch:
        batch.add_column(sa.Column("system_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    with op.batch_alter_table("chat") as batch:
        batch.drop_column("system_prompt")
