"""custom models and full-text search

Revision ID: 9f1c6a2d7b4e
Revises: 7c1e4a9d2b30
Create date: 2026-09-14 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import velox_ui.db.types

revision: str = "9f1c6a2d7b4e"
down_revision: str | None = "7c1e4a9d2b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLite: `message` and `chat` keep their implicit rowid (neither table is declared
# WITHOUT ROWID), so an external-content FTS5 table can index on it directly and a
# search result joins back with `<table>.rowid = <table>_fts.rowid` — no extra column
# needed to bridge the ULID primary key and the rowid FTS5 requires.
_SQLITE_FTS_DDL = (
    "CREATE VIRTUAL TABLE message_fts USING fts5"
    "(content, content='message', content_rowid='rowid')",
    "CREATE VIRTUAL TABLE chat_fts USING fts5(title, content='chat', content_rowid='rowid')",
    """
    CREATE TRIGGER message_fts_ai AFTER INSERT ON message BEGIN
      INSERT INTO message_fts(rowid, content) VALUES (new.rowid, new.content);
    END
    """,
    """
    CREATE TRIGGER message_fts_ad AFTER DELETE ON message BEGIN
      INSERT INTO message_fts(message_fts, rowid, content)
      VALUES('delete', old.rowid, old.content);
    END
    """,
    """
    CREATE TRIGGER message_fts_au AFTER UPDATE ON message BEGIN
      INSERT INTO message_fts(message_fts, rowid, content)
      VALUES('delete', old.rowid, old.content);
      INSERT INTO message_fts(rowid, content) VALUES (new.rowid, new.content);
    END
    """,
    """
    CREATE TRIGGER chat_fts_ai AFTER INSERT ON chat BEGIN
      INSERT INTO chat_fts(rowid, title) VALUES (new.rowid, new.title);
    END
    """,
    """
    CREATE TRIGGER chat_fts_ad AFTER DELETE ON chat BEGIN
      INSERT INTO chat_fts(chat_fts, rowid, title) VALUES('delete', old.rowid, old.title);
    END
    """,
    """
    CREATE TRIGGER chat_fts_au AFTER UPDATE ON chat BEGIN
      INSERT INTO chat_fts(chat_fts, rowid, title) VALUES('delete', old.rowid, old.title);
      INSERT INTO chat_fts(rowid, title) VALUES (new.rowid, new.title);
    END
    """,
)

_SQLITE_FTS_DROP = (
    "DROP TRIGGER IF EXISTS chat_fts_au",
    "DROP TRIGGER IF EXISTS chat_fts_ad",
    "DROP TRIGGER IF EXISTS chat_fts_ai",
    "DROP TRIGGER IF EXISTS message_fts_au",
    "DROP TRIGGER IF EXISTS message_fts_ad",
    "DROP TRIGGER IF EXISTS message_fts_ai",
    "DROP TABLE IF EXISTS chat_fts",
    "DROP TABLE IF EXISTS message_fts",
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "custom_model",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("owner_id", sa.String(length=26), nullable=True),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(length=1024), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("params", velox_ui.db.types.PackedJson(), nullable=True),
        sa.Column("tools", velox_ui.db.types.PackedJson(), nullable=True),
        sa.Column("knowledge_ids", velox_ui.db.types.PackedJson(), nullable=True),
        sa.Column("fallback_chain", velox_ui.db.types.PackedJson(), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_custom_model_slug"),
    )

    with op.batch_alter_table("chat", schema=None) as batch_op:
        batch_op.add_column(sa.Column("custom_model_id", sa.String(length=26), nullable=True))
        batch_op.create_foreign_key(
            "fk_chat_custom_model_id",
            "custom_model",
            ["custom_model_id"],
            ["id"],
            ondelete="SET NULL",
        )

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for statement in _SQLITE_FTS_DDL:
            op.execute(statement)
    else:
        op.execute("ALTER TABLE message ADD COLUMN tsv tsvector")
        op.execute("UPDATE message SET tsv = to_tsvector('english', coalesce(content, ''))")
        op.execute(
            "CREATE OR REPLACE FUNCTION message_tsv_trigger() RETURNS trigger AS $$"
            " BEGIN NEW.tsv := to_tsvector('english', coalesce(NEW.content, '')); "
            "RETURN NEW; END $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER message_tsv_update BEFORE INSERT OR UPDATE OF content "
            "ON message FOR EACH ROW EXECUTE FUNCTION message_tsv_trigger()"
        )
        op.execute("CREATE INDEX ix_message_tsv ON message USING GIN (tsv)")

        op.execute("ALTER TABLE chat ADD COLUMN tsv tsvector")
        op.execute("UPDATE chat SET tsv = to_tsvector('english', coalesce(title, ''))")
        op.execute(
            "CREATE OR REPLACE FUNCTION chat_tsv_trigger() RETURNS trigger AS $$"
            " BEGIN NEW.tsv := to_tsvector('english', coalesce(NEW.title, '')); "
            "RETURN NEW; END $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER chat_tsv_update BEFORE INSERT OR UPDATE OF title "
            "ON chat FOR EACH ROW EXECUTE FUNCTION chat_tsv_trigger()"
        )
        op.execute("CREATE INDEX ix_chat_tsv ON chat USING GIN (tsv)")


def downgrade() -> None:
    """Revert the migration."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for statement in _SQLITE_FTS_DROP:
            op.execute(statement)
    else:
        op.execute("DROP INDEX IF EXISTS ix_chat_tsv")
        op.execute("DROP TRIGGER IF EXISTS chat_tsv_update ON chat")
        op.execute("DROP FUNCTION IF EXISTS chat_tsv_trigger()")
        op.execute("ALTER TABLE chat DROP COLUMN IF EXISTS tsv")
        op.execute("DROP INDEX IF EXISTS ix_message_tsv")
        op.execute("DROP TRIGGER IF EXISTS message_tsv_update ON message")
        op.execute("DROP FUNCTION IF EXISTS message_tsv_trigger()")
        op.execute("ALTER TABLE message DROP COLUMN IF EXISTS tsv")

    with op.batch_alter_table("chat", schema=None) as batch_op:
        batch_op.drop_constraint("fk_chat_custom_model_id", type_="foreignkey")
        batch_op.drop_column("custom_model_id")

    op.drop_table("custom_model")
