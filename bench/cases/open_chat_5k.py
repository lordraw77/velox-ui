"""Opening a conversation of five thousand messages.

Target: under 150 ms end to end. This case measures the database and assembly half of
that budget — one indexed range scan plus the in-memory tree walk that produces the
active path and the sibling counts. The HTTP and rendering halves join the case when
the chat endpoint and the frontend exist, in phases 2 and 3.
"""

from __future__ import annotations

from bench.cases._seed import build_database
from bench.harness import BenchCase, BenchContext, Measurement, timed
from velox_ui.db.repositories.chats import ChatRepository

MESSAGES = 5_000
TARGET_MS = 150.0


async def run(context: BenchContext) -> Measurement:
    """Measure loading the active path of a long conversation."""
    rounds = 5 if context.quick else 30
    database, _, chat_id = await build_database(context.scratch("open_chat"), messages=MESSAGES)
    assert chat_id is not None
    try:
        async with database.session() as session:
            repository = ChatRepository(session)
            loaded = await repository.load_active_path(chat_id)

            async def load() -> None:
                await repository.load_active_path(chat_id)

            samples = await timed(load, rounds=rounds)
    finally:
        await database.dispose()

    ordered = sorted(samples)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    return Measurement(
        value=p95,
        unit="ms",
        target=TARGET_MS,
        samples=tuple(samples),
        detail=(
            f"{len(loaded)} messages on the active path; database and tree assembly only, "
            "HTTP and rendering are added in phases 2 and 3"
        ),
    )


CASE = BenchCase(
    name="open_chat_5k",
    description="Load the active path of a 5 000-message conversation",
    run=run,
    phase=1,
)
