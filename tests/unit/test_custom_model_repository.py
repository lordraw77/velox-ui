"""Custom model (persona) storage."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from velox_ui.db.engine import Database
from velox_ui.db.migrate import upgrade_to_head
from velox_ui.db.repositories.custom_models import CustomModelRepository, FallbackEntry
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


async def test_create_and_get(database: Database, user_id: str) -> None:
    async with database.write() as session:
        model = await CustomModelRepository(session).create(
            owner_id=user_id,
            slug="research-buddy",
            name="Research Buddy",
            system_prompt="You are meticulous.",
            params={"temperature": 0.2},
            fallback_chain=[FallbackEntry(provider_id="ollama-0", model_key="llama3.2")],
        )
        await session.flush()
        model_id = model.id

    async with database.session() as session:
        fetched = await CustomModelRepository(session).get(model_id)
        assert fetched is not None
        assert fetched.name == "Research Buddy"
        assert fetched.fallback_chain == [{"provider_id": "ollama-0", "model_key": "llama3.2"}]
        assert fetched.tools is None
        assert fetched.knowledge_ids is None


async def test_slug_lookup_and_visibility_filter(database: Database, user_id: str) -> None:
    async with database.write() as session:
        users = UserRepository(session)
        other = await users.create(
            email="other@homelab.local", name="Other", password_hash=None
        )
        repository = CustomModelRepository(session)
        await repository.create(
            owner_id=user_id, slug="mine", name="Mine", visibility="private"
        )
        await repository.create(
            owner_id=other.id, slug="theirs-public", name="Theirs", visibility="public"
        )
        await repository.create(
            owner_id=other.id, slug="theirs-private", name="Hidden", visibility="private"
        )
        await session.flush()
        other_id = other.id

    async with database.session() as session:
        repository = CustomModelRepository(session)
        by_slug = await repository.by_slug("mine")
        assert by_slug is not None and by_slug.owner_id == user_id

        visible = await repository.list_visible(user_id=user_id)
        names = {model.name for model in visible}
        assert names == {"Mine", "Theirs"}

    async with database.session() as session:
        visible_to_other = await CustomModelRepository(session).list_visible(user_id=other_id)
        assert {model.name for model in visible_to_other} == {"Theirs", "Hidden"}


async def test_update_and_delete_are_owner_scoped(database: Database, user_id: str) -> None:
    async with database.write() as session:
        users = UserRepository(session)
        other = await users.create(
            email="other@homelab.local", name="Other", password_hash=None
        )
        model = await CustomModelRepository(session).create(
            owner_id=user_id, slug="scoped", name="Scoped"
        )
        await session.flush()
        model_id, other_id = model.id, other.id

    async with database.write() as session:
        repository = CustomModelRepository(session)
        assert not await repository.update(model_id, owner_id=other_id, name="Hijacked")
        assert await repository.update(model_id, owner_id=user_id, name="Renamed")
        assert not await repository.delete(model_id, owner_id=other_id)

    async with database.session() as session:
        fetched = await CustomModelRepository(session).get(model_id)
        assert fetched is not None and fetched.name == "Renamed"

    async with database.write() as session:
        assert await CustomModelRepository(session).delete(model_id, owner_id=user_id)

    async with database.session() as session:
        assert await CustomModelRepository(session).get(model_id) is None
