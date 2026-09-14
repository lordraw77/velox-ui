"""Folder storage: nesting, renaming, moving and deletion."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.models import Base
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.folders import FolderRepository
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


async def test_create_and_list(database: Database, user_id: str) -> None:
    async with database.write() as session:
        repository = FolderRepository(session)
        root = await repository.create(user_id=user_id, name="Work")
        await session.flush()
        await repository.create(user_id=user_id, name="Deep dive", parent_id=root.id)

    async with database.session() as session:
        folders = await FolderRepository(session).list_all(user_id=user_id)

    assert {folder.name for folder in folders} == {"Work", "Deep dive"}
    child = next(folder for folder in folders if folder.name == "Deep dive")
    assert child.parent_id is not None


async def test_rename_and_move(database: Database, user_id: str) -> None:
    async with database.write() as session:
        repository = FolderRepository(session)
        a = await repository.create(user_id=user_id, name="A")
        b = await repository.create(user_id=user_id, name="B")
        await session.flush()
        a_id, b_id = a.id, b.id

    async with database.write() as session:
        repository = FolderRepository(session)
        assert await repository.rename(a_id, user_id=user_id, name="A renamed")
        assert await repository.move(b_id, user_id=user_id, parent_id=a_id, sort_order=5)

    async with database.session() as session:
        folders = {f.id: f for f in await FolderRepository(session).list_all(user_id=user_id)}
    assert folders[a_id].name == "A renamed"
    assert folders[b_id].parent_id == a_id
    assert folders[b_id].sort_order == 5


async def test_delete_detaches_chats_and_cascades_children(
    database: Database, user_id: str
) -> None:
    async with database.write() as session:
        folders = FolderRepository(session)
        parent = await folders.create(user_id=user_id, name="Parent")
        await session.flush()
        child = await folders.create(user_id=user_id, name="Child", parent_id=parent.id)
        chat = await ChatRepository(session).create(
            user_id=user_id, title="In folder", folder_id=parent.id
        )
        await session.flush()
        parent_id, child_id, chat_id = parent.id, child.id, chat.id

    async with database.write() as session:
        assert await FolderRepository(session).delete(parent_id, user_id=user_id)

    async with database.session() as session:
        remaining = await FolderRepository(session).list_all(user_id=user_id)
        assert all(folder.id != child_id for folder in remaining)
        moved_chat = await ChatRepository(session).get(chat_id, user_id=user_id)
        assert moved_chat is not None
        assert moved_chat.folder_id is None


async def test_delete_missing_folder_is_reported(database: Database, user_id: str) -> None:
    async with database.write() as session:
        assert not await FolderRepository(session).delete("nope", user_id=user_id)
