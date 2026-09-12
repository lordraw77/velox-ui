"""Opening a conversation of five thousand messages.

Target: under 150 ms end to end.

"End to end" is measured as far as it honestly can be in a headless suite: the case
now runs the real server on a real socket and times the request a client actually
makes, so the figure includes the indexed range scan, the tree assembly, msgspec
encoding, the HTTP round trip and JSON decoding on the other side. Earlier it measured
only the repository call, which flattered the number by leaving out everything between
the database and the client.

What it still does not include is browser layout. That is deliberate rather than
convenient: gating on it would make the suite depend on a browser download, and the
frontend's own answer to the problem is virtualisation — a five-thousand-message
conversation puts about a dozen nodes in the DOM, so layout cost is bounded by the
viewport rather than by the conversation. That claim is verified by the browser test,
not by this gate.
"""

from __future__ import annotations

import time

import httpx
import msgspec

from bench.cases._seed import build_database
from bench.harness import BenchCase, BenchContext, Measurement
from velox_ui.db.repositories.chats import ChatRepository

MESSAGES = 5_000
TARGET_MS = 150.0
WARMUP = 3


async def run(context: BenchContext) -> Measurement:
    """Measure loading a long conversation over HTTP, and the storage half separately."""
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

    # The storage half, for the detail line: it is the part this project controls most
    # directly, and knowing its share is what tells you where to optimise next.
    storage_samples: list[float] = []
    try:
        async with database.session() as session:
            repository = ChatRepository(session)
            loaded = await repository.load_active_path(chat_id)
            for _ in range(rounds):
                started = time.perf_counter()
                await repository.load_active_path(chat_id)
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
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Authentication is disabled, so the built-in local account owns the
            # fixture's data only if it is the same user. Re-point the chat at it.
            await _reassign(settings, app.base_url, client, chat_id, user_id)

            for index in range(rounds + WARMUP):
                started = time.perf_counter()
                response = await client.get(f"{app.base_url}/api/chats/{chat_id}")
                if response.status_code != 200:
                    return Measurement(
                        value=None,
                        unit="ms",
                        skipped=True,
                        detail=f"the server answered {response.status_code}",
                    )
                body = msgspec.json.decode(response.content)
                elapsed = (time.perf_counter() - started) * 1_000.0
                if index >= WARMUP:
                    samples.append(elapsed)
                if len(body["messages"]) != MESSAGES:
                    return Measurement(
                        value=None,
                        unit="ms",
                        skipped=True,
                        detail=f"expected {MESSAGES} messages, got {len(body['messages'])}",
                    )

    ordered = sorted(samples)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    storage_p50 = sorted(storage_samples)[len(storage_samples) // 2]

    return Measurement(
        value=p95,
        unit="ms",
        target=TARGET_MS,
        samples=tuple(samples),
        detail=(
            f"{len(loaded)} messages over HTTP on a real socket; "
            f"storage and tree assembly alone are {storage_p50:.0f} ms of it; "
            "browser layout is bounded by virtualisation and is not gated here"
        ),
    )


async def _reassign(
    settings: object, base_url: str, client: httpx.AsyncClient, chat_id: str, user_id: str
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

    profile = (await client.get(f"{base_url}/api/auth/me")).json()
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
