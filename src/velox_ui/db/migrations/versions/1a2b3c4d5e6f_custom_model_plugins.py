"""custom model plugins

Revision ID: 1a2b3c4d5e6f
Revises: 5e2b8f14c6a1
Create date: 2026-09-15 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import velox_ui.db.types

revision: str = "1a2b3c4d5e6f"
down_revision: str | None = "5e2b8f14c6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("custom_model", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("plugins", velox_ui.db.types.PackedJson(), nullable=True)
        )


def downgrade() -> None:
    """Revert the migration."""
    with op.batch_alter_table("custom_model", schema=None) as batch_op:
        batch_op.drop_column("plugins")
