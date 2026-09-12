"""The off-path stream writer (ADR-0005).

A completion is persisted *beside* the stream, never in front of it. Three rules make
that safe:

* **Identifiers are allocated in-process**, so the assistant message id exists — and
  can be sent to the client — before any row does.
* **Token text is flushed on an interval**, as a whole-value ``UPDATE`` rather than an
  append per token. A crash can therefore lose up to one interval of text; that is an
  explicit trade against adding a database write to every token.
* **Flushes are serialised.** Each flush writes the full accumulated text, so writes
  are idempotent, but they must still land in order: an older, shorter snapshot
  arriving after a newer one would truncate the message. A lock, not a hope.

The writer also finalises a message the client abandoned. A disconnect cancels the
generator, but the partial answer the model already produced is still the user's, so
the ``finally`` path marks it ``stopped`` and keeps the text.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import update

from velox_ui.clock import now_ms
from velox_ui.db.models import Chat, Message
from velox_ui.metrics import METRICS
from velox_ui.state import AppState

__all__ = ["FLUSH_INTERVAL_MS", "StreamWriter"]

_log = logging.getLogger("velox.persistence")

FLUSH_INTERVAL_MS = 750


class StreamWriter:
    """Accumulates a streaming assistant message and persists it out of band.

    Args:
        state: Application state, for the database and the background task set.
        chat_id: The conversation being appended to.
        message_id: The pre-allocated assistant message id.
    """

    __slots__ = ("_chat_id", "_last_flush_ms", "_lock", "_message_id", "_parts", "_state")

    def __init__(self, state: AppState, *, chat_id: str, message_id: str) -> None:
        """Start an empty writer."""
        self._state = state
        self._chat_id = chat_id
        self._message_id = message_id
        self._parts: list[str] = []
        self._last_flush_ms = now_ms()
        self._lock = asyncio.Lock()

    @property
    def text(self) -> str:
        """The text accumulated so far."""
        return "".join(self._parts)

    def append(self, text: str) -> None:
        """Record a token. Never touches the database."""
        self._parts.append(text)

    def maybe_flush(self) -> None:
        """Schedule a flush if the interval has elapsed.

        Called once per token, so it does nothing but compare two integers in the
        common case.
        """
        moment = now_ms()
        if moment - self._last_flush_ms < FLUSH_INTERVAL_MS:
            return
        self._last_flush_ms = moment
        self._state.schedule(self._flush(self.text))

    async def _flush(self, snapshot: str) -> None:
        """Write one snapshot of the accumulated text."""
        async with self._lock:
            async with self._state.db.write() as session:
                await session.execute(
                    update(Message)
                    .where(Message.id == self._message_id)
                    .values(content=snapshot)
                )
            METRICS.background_writes.labels(kind="stream_flush").inc()

    async def finalize(
        self,
        *,
        status: str,
        reasoning: str | None = None,
        model_ref: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_micros: int | None = None,
        timings: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Write the final state of the message and bump the conversation.

        Args:
            status: ``complete``, ``stopped`` or ``error``.
            reasoning: Separated thinking text, when the model emitted any.
            model_ref: Which model actually served the turn, after any fallback.
            tokens_in: Prompt tokens, as reported by the backend.
            tokens_out: Generated tokens, as reported by the backend.
            cost_micros: Estimated spend in micro-cents. Zero for local models.
            timings: TTFT and generation metrics.
            error: Typed error payload when ``status`` is ``error``.
        """
        async with self._lock:
            async with self._state.db.write() as session:
                await session.execute(
                    update(Message)
                    .where(Message.id == self._message_id)
                    .values(
                        content=self.text,
                        reasoning=reasoning,
                        status=status,
                        model_ref=model_ref,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cost_micros=cost_micros,
                        timings=timings,
                        error=error,
                    )
                )
                await session.execute(
                    update(Chat)
                    .where(Chat.id == self._chat_id)
                    .values(
                        active_leaf_id=self._message_id,
                        updated_at=now_ms(),
                        message_count=Chat.message_count + 2,
                        model_ref=model_ref,
                    )
                )
            METRICS.background_writes.labels(kind="turn_final").inc()
        _log.debug(
            "persisted completion",
            extra={"message_id": self._message_id, "status": status, "chars": len(self.text)},
        )
