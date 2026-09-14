"""Full-text search over chat titles and message content (SQLite FTS5)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.migrate import upgrade_to_head
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.search import SearchRepository
from velox_ui.db.repositories.users import UserRepository


@pytest.fixture
async def database(tmp_path) -> AsyncIterator[Database]:
    path = tmp_path / "repo.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    await upgrade_to_head(url)
    db = Database(url)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def user_id(database: Database) -> str:
    async with database.write() as session:
        user = await UserRepository(session).create(
            email="owner@homelab.local", name="Owner", password_hash=None, role="admin"
        )
        await session.flush()
        return user.id


async def test_search_finds_message_content(database: Database, user_id: str) -> None:
    async with database.write() as session:
        chats = ChatRepository(session)
        chat = await chats.create(user_id=user_id, title="Unrelated title")
        await session.flush()
        await chats.append_message(
            chat_id=chat.id, parent_id=None, role="user", content="the quick brown fox"
        )
        await session.flush()
        chat_id = chat.id

    async with database.session() as session:
        page = await SearchRepository(session, is_sqlite=True).search(
            user_id=user_id, query="quick fox"
        )

    assert len(page.items) == 1
    hit = page.items[0]
    assert hit.kind == "message"
    assert hit.chat_id == chat_id
    assert "[" in hit.snippet  # snippet() wraps the match


async def test_search_finds_chat_titles(database: Database, user_id: str) -> None:
    async with database.write() as session:
        chat = await ChatRepository(session).create(user_id=user_id, title="Kubernetes notes")
        await session.flush()
        chat_id = chat.id

    async with database.session() as session:
        page = await SearchRepository(session, is_sqlite=True).search(
            user_id=user_id, query="kubernetes"
        )

    assert [hit.kind for hit in page.items] == ["chat_title"]
    assert page.items[0].chat_id == chat_id


async def test_search_is_scoped_to_the_caller(database: Database, user_id: str) -> None:
    async with database.write() as session:
        users = UserRepository(session)
        other = await users.create(
            email="other@homelab.local", name="Other", password_hash=None
        )
        await ChatRepository(session).create(user_id=other.id, title="Secret plans")
        await session.flush()

    async with database.session() as session:
        page = await SearchRepository(session, is_sqlite=True).search(
            user_id=user_id, query="secret"
        )
    assert page.items == ()


async def test_search_special_characters_do_not_raise(database: Database, user_id: str) -> None:
    async with database.write() as session:
        await ChatRepository(session).create(user_id=user_id, title="Just a chat")
        await session.flush()

    async with database.session() as session:
        # FTS5 syntax characters must be treated as literal text, not raise.
        page = await SearchRepository(session, is_sqlite=True).search(
            user_id=user_id, query='"unterminated OR AND * ('
        )
    assert page.items == ()


async def test_deleted_chats_are_excluded(database: Database, user_id: str) -> None:
    async with database.write() as session:
        chats = ChatRepository(session)
        chat = await chats.create(user_id=user_id, title="Ephemeral notes")
        await session.flush()
        await chats.soft_delete(chat.id, user_id=user_id)

    async with database.session() as session:
        page = await SearchRepository(session, is_sqlite=True).search(
            user_id=user_id, query="ephemeral"
        )
    assert page.items == ()
