"""Turns that outlive the connection that started them.

A turn used to be the request: ``PreparedTurn.stream()`` was handed straight to the
response, so the moment the client went away — another chat opened, a page reloaded, a
laptop closed — the generator was cancelled and the model stopped mid-answer with
whatever it had produced kept (ADR-0005). That is the right behaviour for a cancelled
request and the wrong one for a reader who simply looked somewhere else.

So the turn runs as its own task and writes its frames here. The response is a reader
of that buffer, and a reader going away means nothing to the producer. Coming back —
the same chat opened again, or the page reloaded — attaches a new reader that replays
the frames from the start, which is enough to rebuild the whole turn on screen: the
event protocol is self-describing, beginning with the ``start`` frame carrying both
message ids (docs/design/03-http-api.md).

In-process, single-worker state, like the approval gate (ADR-0020) and model jobs
(ADR-0017), and for the same reason: a turn in flight is meaningless after the restart
that killed the generation producing it.

Two things are deliberately not buffered. Heartbeats are a property of a connection,
not of a turn, so each reader emits its own while it waits. And a finished turn is kept
only briefly (:data:`RETENTION_S`), long enough for a reader that arrives just after
the end to still receive the answer, after which the persisted message is the record.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from velox_ui.api.sse import HEARTBEAT, encode_event

__all__ = ["RETENTION_S", "ActiveTurn", "TurnBroker"]

_log = logging.getLogger("velox.turns")

HEARTBEAT_INTERVAL_S = 15.0
# How long a finished turn's frames stay readable, for a reader that arrives late.
RETENTION_S = 300.0
# A safety valve, not a budget: an answer this long is a runaway, not a conversation.
MAX_BUFFER_BYTES = 4 * 1024 * 1024


class ActiveTurn:
    """One turn's frames, and the readers following them."""

    __slots__ = ("_bytes", "_updated", "chat_id", "done", "finished_at", "frames", "task")

    def __init__(self, chat_id: str) -> None:
        """Create an empty, running turn."""
        self.chat_id = chat_id
        self.frames: list[bytes] = []
        self.done = False
        self.finished_at: float | None = None
        self.task: asyncio.Task[None] | None = None
        self._bytes = 0
        self._updated = asyncio.Event()

    def append(self, frame: bytes) -> None:
        """Record a frame and wake every reader."""
        self.frames.append(frame)
        self._bytes += len(frame)
        self._updated.set()

    def finish(self) -> None:
        """Mark the turn complete; readers stop once they have caught up."""
        self.done = True
        self.finished_at = time.monotonic()
        self._updated.set()

    @property
    def overflowing(self) -> bool:
        """Whether the buffer has passed the safety valve."""
        return self._bytes > MAX_BUFFER_BYTES

    async def read(self, *, start: int = 0) -> AsyncIterator[bytes]:
        """Yield frames from ``start``, then follow the turn until it ends.

        Args:
            start: Index of the first frame to send. ``0`` replays the whole turn,
                which is what a reader attaching to a turn in progress wants.
        """
        index = max(start, 0)
        while True:
            while index < len(self.frames):
                yield self.frames[index]
                index += 1
            if self.done:
                return
            self._updated.clear()
            try:
                async with asyncio.timeout(HEARTBEAT_INTERVAL_S):
                    await self._updated.wait()
            except TimeoutError:
                # This reader's connection needs traffic; the turn does not.
                yield HEARTBEAT


class TurnBroker:
    """The turns running right now, one per conversation."""

    __slots__ = ("_spawn", "_turns")

    def __init__(self, spawn: Callable[[Coroutine[Any, Any, Any]], asyncio.Task[Any]]) -> None:
        """Create an empty broker.

        Args:
            spawn: Starts a tracked background task — ``AppState.spawn``, whose whole
                purpose is work that must survive the cancellation of whatever
                requested it.
        """
        self._spawn = spawn
        self._turns: dict[str, ActiveTurn] = {}

    def active(self, chat_id: str) -> ActiveTurn | None:
        """Return the turn running for a conversation, or ``None``."""
        self._evict()
        turn = self._turns.get(chat_id)
        return turn if turn is not None and not turn.done else None

    def readable(self, chat_id: str) -> ActiveTurn | None:
        """Return a conversation's turn if it can still be read: running, or just ended."""
        self._evict()
        return self._turns.get(chat_id)

    def start(self, chat_id: str, source: AsyncIterator[bytes]) -> ActiveTurn:
        """Run ``source`` as a background turn for ``chat_id`` and return it.

        Raises:
            RuntimeError: If a turn is already running for this conversation. The
                caller turns that into a 409: two turns writing one conversation would
                interleave their messages.
        """
        self._evict()
        existing = self._turns.get(chat_id)
        if existing is not None and not existing.done:
            raise RuntimeError(f"a turn is already running for conversation {chat_id}")

        turn = ActiveTurn(chat_id)
        self._turns[chat_id] = turn
        turn.task = self._spawn(self._run(turn, source))
        return turn

    async def cancel(self, chat_id: str) -> bool:
        """Stop the turn running for a conversation. Returns whether there was one."""
        turn = self.active(chat_id)
        if turn is None or turn.task is None:
            return False
        turn.task.cancel()
        # The producer's own cleanup persists what the model produced (ADR-0005); this
        # only waits for it to unwind so `done` is in the buffer before we answer.
        await asyncio.gather(turn.task, return_exceptions=True)
        return True

    async def _run(self, turn: ActiveTurn, source: AsyncIterator[bytes]) -> None:
        """Consume the turn's frames into its buffer, whatever the readers do."""
        try:
            async for frame in source:
                if frame == HEARTBEAT:
                    continue  # a reader's concern, not a turn's
                turn.append(frame)
                if turn.overflowing:
                    _log.warning(
                        "turn buffer over %d bytes; stopping it",
                        MAX_BUFFER_BYTES,
                        extra={"chat_id": turn.chat_id},
                    )
                    turn.append(
                        encode_event(
                            "error",
                            {
                                "code": "upstream_error",
                                "message": "The reply grew past what one turn may buffer.",
                                "retryable": False,
                            },
                        )
                    )
                    turn.append(encode_event("done", {"finish_reason": "error"}))
                    return
        except asyncio.CancelledError:
            # Stopped on purpose. The generator's own `finally` keeps what was produced;
            # readers need an end, and cancellation cannot be yielded through.
            turn.append(encode_event("done", {"finish_reason": "stopped"}))
            raise
        except Exception:
            _log.exception("turn failed", extra={"chat_id": turn.chat_id})
            turn.append(
                encode_event(
                    "error",
                    {"code": "internal", "message": "The turn failed.", "retryable": False},
                )
            )
            turn.append(encode_event("done", {"finish_reason": "error"}))
        finally:
            turn.finish()

    def _evict(self) -> None:
        """Drop finished turns nobody can still want."""
        now = time.monotonic()
        for chat_id, turn in list(self._turns.items()):
            if turn.finished_at is not None and now - turn.finished_at > RETENTION_S:
                del self._turns[chat_id]
