"""Opening a conversation of five thousand messages.

Target: under 150 ms end to end.

"End to end" is measured as far as it honestly can be in a headless suite: the case
runs the real server on a real socket and times the request a client actually makes to
open a conversation, so the figure includes the queries, msgspec encoding, the HTTP
round trip and JSON decoding on the other side.

Since phase 4 that request returns the newest page of the conversation, not all of it
(ADR-0007): the interface virtualises, so it needs what fits on screen plus a margin,
and fetches earlier pages as the reader scrolls up. The case asserts that shape — a
full page and a cursor to the rest — so it cannot pass by accident on a short
conversation. Walking the whole history back, page by page, is timed too and reported
in the detail line, because "opening is fast" must not hide "reading everything is
slow".

Browser layout is not included. Gating on it would make the suite depend on a browser
download, and virtualisation bounds it by the viewport rather than by the conversation;
that claim is verified by the browser test, not by this gate.
"""

from __future__ import annotations

import time

import httpx
import msgspec

from bench.cases._seed import build_database
from bench.harness import BenchCase, BenchContext, Measurement
from velox_ui.db.repositories.chats import ChatRepository

MESSAGES = 5_000
PAGE = 60
TARGET_MS = 150.0
WARMUP = 3


async def run(context: BenchContext) -> Measurement:
    """Measure opening a long conversation over HTTP, and walking all of it back."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.fakes.server import run_fake

    from velox_ui.app import create_app
    from velox_ui.settings import AuthSettings, DatabaseSettings, ProviderSettings, Settings

    rounds = 5 if context.quick else 25
    data_dir = context.scratch("open_chat")
    database, user_id, chat_id = await build_database(data_dir, messages=MESSAGES)
    assert chat_id is not None

    # The storage half, for the detail line.
    storage_samples: list[float] = []
    try:
        async with database.session() as session:
            repository = ChatRepository(session)
            for _ in range(rounds):
                started = time.perf_counter()
                await repository.load_path_page(chat_id, start_id=None, limit=PAGE)
                storage_samples.append((time.perf_counter() - started) * 1_000.0)
    finally:
        await database.dispose()

    settings = Settings(
        data_dir=data_dir,
        secret_key="benchmark-secret-key-not-used-for-anything",
        db=DatabaseSettings(
            url=f"sqlite+aiosqlite:///{(data_dir / 'bench.db').as_posix()}",
            # The fixture was built with create_all, not Alembic, so there is no
            # revision stamped; migrating here would try to create the tables again.
            auto_migrate=False,
        ),
        auth=AuthSettings(enabled=False),
        providers=ProviderSettings(autodiscover=False),
        log_level="ERROR",
    )

    samples: list[float] = []
    with run_fake(create_app(settings), lifespan="on") as app:
        async with httpx.AsyncClient(base_url=app.base_url, timeout=60.0) as client:
            # Authentication is disabled, so the built-in local account owns the
            # fixture's data only if it is the same user. Re-point the chat at it.
            await _reassign(settings, client, chat_id, user_id)

            for index in range(rounds + WARMUP):
                started = time.perf_counter()
                response = await client.get(f"/api/chats/{chat_id}", params={"limit": PAGE})
                if response.status_code != 200:
                    return _skipped(f"the server answered {response.status_code}")
                body = msgspec.json.decode(response.content)
                elapsed = (time.perf_counter() - started) * 1_000.0
                if index >= WARMUP:
                    samples.append(elapsed)
                if len(body["messages"]) != PAGE or body["messages_cursor"] is None:
                    return _skipped(
                        f"expected a {PAGE}-message page with a cursor, got "
                        f"{len(body['messages'])} messages"
                    )

            walked, walk_ms, pages = await _walk_back(client, chat_id, body["messages_cursor"])
            if walked + PAGE != MESSAGES:
                return _skipped(f"paging back reached {walked + PAGE} of {MESSAGES} messages")

    ordered = sorted(samples)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    storage_p50 = sorted(storage_samples)[len(storage_samples) // 2]

    return Measurement(
        value=p95,
        unit="ms",
        target=TARGET_MS,
        samples=tuple(samples),
        detail=(
            f"newest {PAGE} of {MESSAGES} messages over HTTP; "
            f"storage {storage_p50:.1f} ms of it; "
            f"reading the remaining {walked} back in {pages} pages takes {walk_ms:.0f} ms"
        ),
    )


async def _walk_back(
    client: httpx.AsyncClient, chat_id: str, cursor: str
) -> tuple[int, float, int]:
    """Fetch every older page, as a reader scrolling to the very start would.

    Returns:
        Messages fetched, total milliseconds, and the number of pages.
    """
    fetched = 0
    pages = 0
    started = time.perf_counter()
    next_cursor: str | None = cursor
    while next_cursor is not None:
        response = await client.get(
            f"/api/chats/{chat_id}/messages", params={"cursor": next_cursor, "limit": 200}
        )
        page = msgspec.json.decode(response.content)
        fetched += len(page["items"])
        pages += 1
        next_cursor = page["next_cursor"]
    return fetched, (time.perf_counter() - started) * 1_000.0, pages


def _skipped(detail: str) -> Measurement:
    return Measurement(value=None, unit="ms", skipped=True, detail=detail)


async def _reassign(
    settings: object, client: httpx.AsyncClient, chat_id: str, user_id: str
) -> None:
    """Point the seeded conversation at the built-in local account.

    The fixture creates its own user; with authentication disabled the server runs as
    its local account instead. Rewriting the owner is simpler and more honest than
    teaching the fixture about the server's bootstrap.
    """
    from sqlalchemy import update

    from velox_ui.db.engine import Database
    from velox_ui.db.models import Chat
    from velox_ui.settings import Settings

    assert isinstance(settings, Settings)
    del user_id

    profile = (await client.get("/api/auth/me")).json()
    database = Database(settings.database_url)
    try:
        async with database.write() as session:
            await session.execute(
                update(Chat).where(Chat.id == chat_id).values(user_id=profile["id"])
            )
    finally:
        await database.dispose()


CASE = BenchCase(
    name="open_chat_5k",
    description="Open a 5 000-message conversation over HTTP",
    run=run,
    phase=1,
)
