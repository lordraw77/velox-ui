"""Paging backwards through a conversation's active branch."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.models import Base
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.users import UserRepository


@pytest.fixture
async def database(tmp_path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'paging.db').as_posix()}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def tree(database: Database) -> dict[str, object]:
    """A 25-message branch with a 4-message alternative branching off message 10."""
    async with database.write() as session:
        user = await UserRepository(session).create(
            email="owner@homelab.local", name="Owner", password_hash=None, role="admin"
        )
        await session.flush()
        repository = ChatRepository(session)
        chat = await repository.create(user_id=user.id, title="Paging")
        await session.flush()

        main: list[str] = []
        parent = None
        for index in range(25):
            message = await repository.append_message(
                chat_id=chat.id,
                parent_id=parent,
                role="user" if index % 2 == 0 else "assistant",
                content=f"main {index}",
            )
            await session.flush()
            main.append(message.id)
            parent = message.id

        branch: list[str] = []
        parent = main[10]
        for index in range(4):
            message = await repository.append_message(
                chat_id=chat.id,
                parent_id=parent,
                role="assistant" if index % 2 == 0 else "user",
                content=f"branch {index}",
            )
            await session.flush()
            branch.append(message.id)
            parent = message.id

        await repository.set_active_leaf(chat.id, main[-1])
    return {"chat_id": chat.id, "main": main, "branch": branch}


async def _walk(repository: ChatRepository, chat_id: str, *, start: str | None, limit: int):
    pages = []
    cursor = start
    while True:
        page, cursor = await repository.load_path_page(chat_id, start_id=cursor, limit=limit)
        pages.append(page)
        if cursor is None:
            return pages
        start = cursor


async def test_pages_reassemble_the_full_branch(database: Database, tree: dict) -> None:
    async with database.session() as session:
        repository = ChatRepository(session)
        pages = await _walk(repository, tree["chat_id"], start=None, limit=7)
        full = await repository.load_active_path(tree["chat_id"])

    assert [len(page) for page in pages] == [7, 7, 7, 4]
    # Newest page first; each page oldest-first, so reversing the page order and
    # concatenating yields the conversation in reading order.
    stitched = [node for page in reversed(pages) for node in page]
    assert [node.id for node in stitched] == tree["main"]
    assert stitched == full, "paging must agree with the full scan, sibling counts included"


async def test_sibling_counts_cover_off_branch_messages(database: Database, tree: dict) -> None:
    async with database.session() as session:
        page, _ = await ChatRepository(session).load_path_page(
            tree["chat_id"], start_id=None, limit=100
        )
    forked = next(node for node in page if node.id == tree["main"][11])
    assert (forked.sibling_index, forked.sibling_count) == (0, 2)


async def test_a_page_can_start_at_another_branch_tip(database: Database, tree: dict) -> None:
    async with database.session() as session:
        page, cursor = await ChatRepository(session).load_path_page(
            tree["chat_id"], start_id=tree["branch"][-1], limit=100
        )
    assert cursor is None
    assert [node.id for node in page] == tree["main"][:11] + tree["branch"]
    assert page[11].sibling_index == 1, "the alternative is the second child of message 10"


async def test_the_first_page_is_bounded_by_the_limit(database: Database, tree: dict) -> None:
    async with database.session() as session:
        page, cursor = await ChatRepository(session).load_path_page(
            tree["chat_id"], start_id=None, limit=5
        )
    assert [node.id for node in page] == tree["main"][-5:]
    assert cursor == tree["main"][-6]


async def test_a_cursor_from_another_chat_yields_nothing(
    database: Database, tree: dict
) -> None:
    async with database.session() as session:
        page, cursor = await ChatRepository(session).load_path_page(
            "01JUNKCHAT0000000000000000", start_id=tree["main"][-1], limit=5
        )
    assert (page, cursor) == ([], None)
