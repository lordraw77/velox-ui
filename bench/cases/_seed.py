"""Shared fixture builder for the database benchmarks.

Rows are inserted with core ``executemany`` rather than the ORM: seeding ten thousand
conversations through the unit of work would measure SQLAlchemy's flush, not the query
under test, and would take long enough to discourage running the suite.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import insert

from velox_ui.clock import now_ms
from velox_ui.db.engine import Database
from velox_ui.db.models import AppUser, Base, Chat, Message
from velox_ui.ids import new_ulid, ulid_at

BATCH = 2_000


async def build_database(
    path: Path, *, chats: int = 0, messages: int = 0
) -> tuple[Database, str, str | None]:
    """Create a populated SQLite database.

    Args:
        path: Directory for the database file.
        chats: How many conversations to create for the listing benchmark.
        messages: How many messages to put in one conversation, as a single unbranched
            chain, which is the shape a long real conversation has.

    Returns:
        The open database, the owner's user id, and the id of the long conversation
        when one was requested.
    """
    database = Database(f"sqlite+aiosqlite:///{(path / 'bench.db').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    user_id = new_ulid()
    moment = now_ms()
    async with database.write() as session:
        await session.execute(
            insert(AppUser),
            [
                {
                    "id": user_id,
                    "email": "bench@velox.local",
                    "email_norm": "bench@velox.local",
                    "name": "Benchmark",
                    "password_hash": None,
                    "role": "admin",
                    "status": "active",
                    "avatar_url": None,
                    "settings": None,
                    "created_at": moment,
                    "last_seen_at": None,
                }
            ],
        )

    long_chat_id: str | None = None

    if chats:
        rows = []
        for index in range(chats):
            chat_id = ulid_at(moment - chats + index, randomness=index)
            rows.append(
                {
                    "id": chat_id,
                    "user_id": user_id,
                    "folder_id": None,
                    "title": f"Conversation {index}",
                    "active_leaf_id": None,
                    "model_ref": "ollama:llama3.2",
                    # A realistic sidebar has a handful of pinned chats, not none:
                    # the index leads with `pinned DESC`, so a listing over a table
                    # where the column is constant would not exercise it.
                    "pinned": index % 500 == 0,
                    "archived": False,
                    "message_count": 2,
                    "meta": None,
                    "created_at": moment - chats + index,
                    "updated_at": moment - chats + index,
                    "deleted_at": None,
                }
            )
        await _insert_many(database, Chat, rows)

    if messages:
        long_chat_id = ulid_at(moment, randomness=chats + 1)
        async with database.write() as session:
            await session.execute(
                insert(Chat),
                [
                    {
                        "id": long_chat_id,
                        "user_id": user_id,
                        "folder_id": None,
                        "title": "Long conversation",
                        "active_leaf_id": None,
                        "model_ref": "ollama:llama3.2",
                        "pinned": False,
                        "archived": False,
                        "message_count": messages,
                        "meta": None,
                        "created_at": moment,
                        "updated_at": moment,
                        "deleted_at": None,
                    }
                ],
            )

        body = (
            "This is a benchmark message body. It is deliberately a few hundred "
            "characters long, because a conversation of one-word messages would "
            "understate how much text the query actually has to move off disk. "
        ) * 2
        rows = []
        parent: str | None = None
        for depth in range(messages):
            message_id = ulid_at(moment + depth, randomness=depth)
            rows.append(
                {
                    "id": message_id,
                    "chat_id": long_chat_id,
                    "parent_id": parent,
                    "role": "user" if depth % 2 == 0 else "assistant",
                    "content": body,
                    "reasoning": None,
                    "status": "complete",
                    "model_ref": "ollama:llama3.2",
                    "depth": depth,
                    "tokens_in": 120,
                    "tokens_out": 240,
                    "cost_micros": 0,
                    "timings": None,
                    "error": None,
                    "meta": None,
                    "created_at": moment + depth,
                }
            )
            parent = message_id
        await _insert_many(database, Message, rows)

        async with database.write() as session:
            from sqlalchemy import update

            await session.execute(
                update(Chat).where(Chat.id == long_chat_id).values(active_leaf_id=parent)
            )

    return database, user_id, long_chat_id


async def _insert_many(database: Database, model: type, rows: list[dict]) -> None:
    """Insert rows in batches, committing as it goes."""
    for start in range(0, len(rows), BATCH):
        async with database.write() as session:
            await session.execute(insert(model), rows[start : start + BATCH])
