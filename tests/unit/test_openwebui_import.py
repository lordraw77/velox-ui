"""Open WebUI import: the tree/legacy shapes, tags, folders, idempotency."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.models import Base
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.folders import FolderRepository
from velox_ui.db.repositories.tags import TagRepository
from velox_ui.db.repositories.users import UserRepository
from velox_ui.services.importers.openwebui import ImportReport, import_export, parse_export

_TREE_EXPORT = b"""
[
  {
    "id": "owu-1",
    "created_at": 1700000000,
    "updated_at": 1700000500,
    "folder_id": "fld-1",
    "chat": {
      "title": "Hello world",
      "models": ["llama3:8b"],
      "tags": ["work", "python"],
      "history": {
        "messages": {
          "m1": {"id": "m1", "parentId": null, "role": "user", "content": "Hi",
                 "timestamp": 1700000000},
          "m2": {"id": "m2", "parentId": "m1", "role": "assistant", "content": "Hello!",
                 "model": "llama3:8b", "timestamp": 1700000100}
        },
        "currentId": "m2"
      }
    }
  }
]
"""

_FLAT_EXPORT = b"""
[
  {
    "id": "owu-2",
    "created_at": 1700001000,
    "chat": {
      "title": "Old style",
      "messages": [
        {"id": "a1", "role": "user", "content": "Old question", "timestamp": 1700001000},
        {"id": "a2", "role": "assistant", "content": "Old answer", "model": "gpt-4o-mini",
         "timestamp": 1700001050}
      ]
    }
  }
]
"""


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


async def test_tree_shaped_export_preserves_branching_and_timestamps(
    database: Database, user_id: str
) -> None:
    entries = parse_export(_TREE_EXPORT)
    async with database.write() as session:
        report = await import_export(
            session, user_id=user_id, entries=entries, folder_names={"fld-1": "Work"}
        )
    assert report == ImportReport(imported=1, skipped=())

    async with database.session() as session:
        chats = ChatRepository(session)
        page, _ = await chats.list_page(user_id=user_id, limit=10)
        assert len(page) == 1
        chat = page[0]
        assert chat.title == "Hello world"
        assert chat.model_ref == "ollama:llama3:8b"
        assert chat.updated_at == 1700000500000
        assert chat.message_count == 2

        path = await chats.load_active_path(chat.id)
        assert [(m.role, m.content) for m in path] == [
            ("user", "Hi"),
            ("assistant", "Hello!"),
        ]
        assert [m.created_at for m in path] == [1700000000000, 1700000100000]

        folders = await FolderRepository(session).list_all(user_id=user_id)
        assert [f.name for f in folders] == ["Work"]
        assert folders[0].id == chat.folder_id

        tags = {t.name for t in await TagRepository(session).list_all(user_id=user_id)}
        assert tags == {"work", "python"}


async def test_legacy_flat_export_becomes_a_linear_branch(
    database: Database, user_id: str
) -> None:
    entries = parse_export(_FLAT_EXPORT)
    async with database.write() as session:
        report = await import_export(session, user_id=user_id, entries=entries)
    assert report.imported == 1

    async with database.session() as session:
        chats = ChatRepository(session)
        page, _ = await chats.list_page(user_id=user_id, limit=10)
        chat = page[0]
        assert chat.model_ref is None  # no chat.models on the legacy shape

        path = await chats.load_active_path(chat.id)
        assert [(m.role, m.content, m.model_ref) for m in path] == [
            ("user", "Old question", None),
            ("assistant", "Old answer", "openai:gpt-4o-mini"),
        ]


async def test_reimporting_the_same_export_is_a_no_op(database: Database, user_id: str) -> None:
    entries = parse_export(_TREE_EXPORT)
    async with database.write() as session:
        await import_export(session, user_id=user_id, entries=entries)
    async with database.write() as session:
        report = await import_export(session, user_id=user_id, entries=entries)

    assert report.imported == 0
    assert len(report.skipped) == 1
    assert report.skipped[0].reason == "already imported"

    async with database.session() as session:
        page, _ = await ChatRepository(session).list_page(user_id=user_id, limit=10)
    assert len(page) == 1


async def test_malformed_entry_is_skipped_not_fatal(database: Database, user_id: str) -> None:
    entries = [*parse_export(_TREE_EXPORT), {"id": "owu-broken"}]
    async with database.write() as session:
        report = await import_export(session, user_id=user_id, entries=entries)

    assert report.imported == 1
    assert len(report.skipped) == 1
    assert report.skipped[0].source_id == "owu-broken"


def test_parse_export_rejects_the_wrong_shape() -> None:
    with pytest.raises(ValueError, match="expected a JSON array"):
        parse_export(b'{"not": "a chat export"}')


def test_parse_export_accepts_the_wrapped_shape() -> None:
    entries = parse_export(b'{"chats": [{"id": "x", "chat": {"messages": []}}]}')
    assert entries == [{"id": "x", "chat": {"messages": []}}]
