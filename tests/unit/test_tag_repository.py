"""Tag storage: creation, attachment and the chat-list tag filter."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.models import Base
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.tags import TagRepository
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


async def test_attach_is_idempotent_and_detach_removes(
    database: Database, user_id: str
) -> None:
    async with database.write() as session:
        tag = await TagRepository(session).create(user_id=user_id, name="work", color="#fff")
        chat = await ChatRepository(session).create(user_id=user_id, title="Chat")
        await session.flush()
        tag_id, chat_id = tag.id, chat.id

    async with database.write() as session:
        repository = TagRepository(session)
        assert await repository.attach(chat_id=chat_id, tag_id=tag_id, user_id=user_id)
        assert await repository.attach(chat_id=chat_id, tag_id=tag_id, user_id=user_id)

    async with database.session() as session:
        tags = await TagRepository(session).for_chat(chat_id)
    assert [tag.name for tag in tags] == ["work"]

    async with database.write() as session:
        assert await TagRepository(session).detach(
            chat_id=chat_id, tag_id=tag_id, user_id=user_id
        )

    async with database.session() as session:
        assert await TagRepository(session).for_chat(chat_id) == ()


async def test_attach_rejects_a_chat_owned_by_someone_else(
    database: Database, user_id: str
) -> None:
    async with database.write() as session:
        users = UserRepository(session)
        other = await users.create(
            email="other@homelab.local", name="Other", password_hash=None
        )
        tag = await TagRepository(session).create(user_id=user_id, name="mine")
        chat = await ChatRepository(session).create(user_id=other.id, title="Not mine")
        await session.flush()
        tag_id, chat_id = tag.id, chat.id

    async with database.write() as session:
        attached = await TagRepository(session).attach(
            chat_id=chat_id, tag_id=tag_id, user_id=user_id
        )
    assert not attached


async def test_chat_list_can_be_filtered_by_tag(database: Database, user_id: str) -> None:
    async with database.write() as session:
        tags = TagRepository(session)
        chats = ChatRepository(session)
        tag = await tags.create(user_id=user_id, name="starred")
        tagged = await chats.create(user_id=user_id, title="Tagged")
        untagged = await chats.create(user_id=user_id, title="Untagged")
        await session.flush()
        assert await tags.attach(chat_id=tagged.id, tag_id=tag.id, user_id=user_id)
        tag_id = tag.id
        _ = untagged

    async with database.session() as session:
        items, _ = await ChatRepository(session).list_page(
            user_id=user_id, limit=10, tag_id=tag_id
        )

    assert [item.title for item in items] == ["Tagged"]


async def test_delete_tag_removes_attachments(database: Database, user_id: str) -> None:
    async with database.write() as session:
        tags = TagRepository(session)
        chat = await ChatRepository(session).create(user_id=user_id, title="Chat")
        tag = await tags.create(user_id=user_id, name="temp")
        await session.flush()
        assert await tags.attach(chat_id=chat.id, tag_id=tag.id, user_id=user_id)
        chat_id, tag_id = chat.id, tag.id

    async with database.write() as session:
        assert await TagRepository(session).delete(tag_id, user_id=user_id)

    async with database.session() as session:
        assert await TagRepository(session).for_chat(chat_id) == ()
