"""Startup background work: one HTTP client, and discovery that never blocks readiness."""

from __future__ import annotations

import asyncio

from velox_ui.db.engine import Database
from velox_ui.settings import Settings
from velox_ui.state import AppState


async def test_the_client_is_built_once_even_when_raced(settings: Settings) -> None:
    database = Database(settings.database_url)
    state = AppState(settings, database)
    try:
        first, second = await asyncio.gather(state.http_client(), state.http_client())
        assert first is second is state.http, "a second client would leak its connection pool"
    finally:
        await state.close_http()
        await database.dispose()


async def test_waiting_for_discovery_never_cancels_it(settings: Settings) -> None:
    database = Database(settings.database_url)
    state = AppState(settings, database)
    gate = asyncio.Event()

    async def slow_discovery() -> None:
        await gate.wait()

    try:
        state.discovery = state.spawn(slow_discovery())
        await state.discovery_settled(timeout_s=0.05)
        assert not state.discovery.done(), "a listing that gave up waiting must not cancel it"

        gate.set()
        await state.discovery_settled(timeout_s=1.0)
        assert state.discovery.done()
    finally:
        await database.dispose()
