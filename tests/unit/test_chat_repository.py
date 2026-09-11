"""Conversation storage: the message tree and keyset listing."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.models import Base
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.users import UserRepository


@pytest.fixture
async def database(tmp_path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'repo.db').as_posix()}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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


async def test_active_path_follows_the_chain(database: Database, user_id: str) -> None:
    async with database.write() as session:
        repository = ChatRepository(session)
        chat = await repository.create(user_id=user_id, title="Test")
        await session.flush()
        parent = None
        for index in range(6):
            message = await repository.append_message(
                chat_id=chat.id,
                parent_id=parent,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
            )
            await session.flush()
            parent = message.id
        await repository.set_active_leaf(chat.id, parent or "", bump=6)
        chat_id = chat.id

    async with database.session() as session:
        path = await ChatRepository(session).load_active_path(chat_id)

    assert [node.content for node in path] == [f"message {index}" for index in range(6)]
    assert [node.depth for node in path] == list(range(6))
    assert all(node.sibling_count == 1 for node in path)


async def test_editing_creates_a_sibling_branch(database: Database, user_id: str) -> None:
    """Regeneration must add a branch, never overwrite the previous answer."""
    async with database.write() as session:
        repository = ChatRepository(session)
        chat = await repository.create(user_id=user_id, title="Branching")
        await session.flush()
        question = await repository.append_message(
            chat_id=chat.id, parent_id=None, role="user", content="question"
        )
        await session.flush()
        first = await repository.append_message(
            chat_id=chat.id, parent_id=question.id, role="assistant", content="first answer"
        )
        second = await repository.append_message(
            chat_id=chat.id, parent_id=question.id, role="assistant", content="second answer"
        )
        await session.flush()
        await repository.set_active_leaf(chat.id, second.id, bump=3)
        chat_id, first_id, second_id = chat.id, first.id, second.id

    async with database.session() as session:
        repository = ChatRepository(session)
        active = await repository.load_active_path(chat_id)
        other = await repository.load_active_path(chat_id, leaf_id=first_id)

    assert active[-1].content == "second answer"
    assert active[-1].sibling_count == 2, "the UI needs to show 2 of 2"
    assert active[-1].id == second_id
    assert other[-1].content == "first answer", "the earlier answer is still reachable"
    assert other[-1].sibling_index == 0


async def test_empty_chat_loads_as_empty(database: Database, user_id: str) -> None:
    async with database.write() as session:
        chat = await ChatRepository(session).create(user_id=user_id, title="Empty")
        await session.flush()
        chat_id = chat.id
    async with database.session() as session:
        assert await ChatRepository(session).load_active_path(chat_id) == []


async def test_keyset_listing_walks_every_chat_once(database: Database, user_id: str) -> None:
    total = 137
    async with database.write() as session:
        repository = ChatRepository(session)
        for index in range(total):
            chat = await repository.create(user_id=user_id, title=f"Chat {index}")
            chat.updated_at = 1_700_000_000_000 + index
            chat.pinned = index % 40 == 0

    seen: list[str] = []
    cursor = None
    async with database.session() as session:
        repository = ChatRepository(session)
        for _ in range(20):
            page, cursor = await repository.list_page(user_id=user_id, limit=25, cursor=cursor)
            seen.extend(item.id for item in page)
            if cursor is None:
                break

    assert len(seen) == total, "pagination must not skip or duplicate rows"
    assert len(set(seen)) == total


async def test_pinned_chats_sort_first(database: Database, user_id: str) -> None:
    async with database.write() as session:
        repository = ChatRepository(session)
        for index in range(10):
            chat = await repository.create(user_id=user_id, title=f"Chat {index}")
            chat.updated_at = 1_700_000_000_000 + index
            chat.pinned = index == 0  # oldest, so only the pin can lift it

    async with database.session() as session:
        page, _ = await ChatRepository(session).list_page(user_id=user_id, limit=10)

    assert page[0].title == "Chat 0"
    assert page[0].pinned


async def test_other_users_cannot_read_a_chat(database: Database, user_id: str) -> None:
    async with database.write() as session:
        users = UserRepository(session)
        intruder = await users.create(
            email="other@homelab.local", name="Other", password_hash=None
        )
        chat = await ChatRepository(session).create(user_id=user_id, title="Private")
        await session.flush()
        chat_id, intruder_id = chat.id, intruder.id

    async with database.session() as session:
        repository = ChatRepository(session)
        assert await repository.get(chat_id, user_id=user_id) is not None
        assert await repository.get(chat_id, user_id=intruder_id) is None


async def test_soft_deleted_chats_leave_the_listing(database: Database, user_id: str) -> None:
    async with database.write() as session:
        repository = ChatRepository(session)
        chat = await repository.create(user_id=user_id, title="Doomed")
        await session.flush()
        chat_id = chat.id
    async with database.write() as session:
        assert await ChatRepository(session).soft_delete(chat_id, user_id=user_id)
    async with database.session() as session:
        page, _ = await ChatRepository(session).list_page(user_id=user_id, limit=10)
    assert page == ()
