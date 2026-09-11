"""Chat listing over ten thousand conversations.

Target: under 30 ms per page. The query is a keyset seek on ``ix_chat_list``; if that
index is ever dropped or the ordering drifts away from it, this case is what notices.
"""

from __future__ import annotations

from bench.cases._seed import build_database
from bench.harness import BenchCase, BenchContext, Measurement, timed
from velox_ui.db.repositories.chats import ChatRepository

CHATS = 10_000
TARGET_MS = 30.0


async def run(context: BenchContext) -> Measurement:
    """Measure first-page and deep-page listing latency."""
    rounds = 20 if context.quick else 200
    database, user_id, _ = await build_database(context.scratch("chat_list"), chats=CHATS)
    try:
        async with database.session() as session:
            repository = ChatRepository(session)

            # Walk deep into the list first: a cursor from page 100 is the case that
            # would collapse under OFFSET, and it is the one worth timing.
            cursor = None
            for _ in range(100):
                _, cursor = await repository.list_page(user_id=user_id, limit=50, cursor=cursor)
                if cursor is None:
                    break

            async def first_page() -> None:
                await repository.list_page(user_id=user_id, limit=50)

            async def deep_page() -> None:
                await repository.list_page(user_id=user_id, limit=50, cursor=cursor)

            first = await timed(first_page, rounds=rounds)
            deep = await timed(deep_page, rounds=rounds) if cursor else first
    finally:
        await database.dispose()

    samples = tuple(first + deep)
    p95 = sorted(samples)[int(0.95 * (len(samples) - 1))]
    return Measurement(
        value=p95,
        unit="ms",
        target=TARGET_MS,
        samples=samples,
        detail=f"{CHATS} chats, 50 per page, first page and page 100 combined",
    )


CASE = BenchCase(
    name="chat_list_10k",
    description="Keyset listing of 10 000 conversations",
    run=run,
    phase=1,
)
