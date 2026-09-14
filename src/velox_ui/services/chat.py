"""Completion turn orchestration.

This is the path the whole project is measured against, so the ordering of work is
deliberate.

A turn happens in two acts. :meth:`ChatService.prepare` resolves the provider, checks
ownership and builds the context — everything that can fail *before* the response
starts, so those failures are ordinary HTTP errors with a status code. Only then does
:meth:`PreparedTurn.stream` begin emitting bytes; once it has, there is no way to send
a status code, and a failure has to travel as an ``error`` event instead.

Inside the stream:

1. The assistant message id is allocated in-process and sent in the ``start`` frame,
   before any row exists.
2. The database writes for the user turn and the empty assistant row are issued
   **concurrently with** opening the upstream request, not before it (ADR-0005).
3. Provider events are forwarded as pre-framed bytes; text is accumulated for the
   background writer and never re-encoded on its way out (ADR-0004).

Heartbeats come from a pump rather than from timing out the provider iterator.
``asyncio.wait_for`` cancels whatever it is waiting on, and cancelling a partially
consumed HTTP read would destroy the generation in order to send a keep-alive. A task
drains the provider into a queue instead, and the timeout applies to ``queue.get()``,
which is free to cancel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from velox_ui.api.sse import HEARTBEAT, encode_event, encode_text_delta
from velox_ui.clock import MonotonicTimer
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.errors import NotFoundError, VeloxError
from velox_ui.ids import new_ulid
from velox_ui.metrics import METRICS
from velox_ui.providers.base import (
    Capabilities,
    ChatMessage,
    ChatRequest,
    Done,
    Provider,
    ReasoningDelta,
    SamplingParams,
    Status,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from velox_ui.providers.errors import ProviderError
from velox_ui.services.context import build_messages
from velox_ui.services.model_params import merge_params
from velox_ui.services.persistence import StreamWriter
from velox_ui.state import AppState

if TYPE_CHECKING:  # pragma: no cover - typing only; keeps rag/ off the cold path.
    from velox_ui.rag.retrieve import RetrievedChunk

__all__ = ["HEARTBEAT_INTERVAL_S", "ChatService", "PreparedTurn"]

_log = logging.getLogger("velox.chat")

HEARTBEAT_INTERVAL_S = 15.0
"""How long a stream may be silent before a keep-alive comment is sent.

Tuned for the slow case, not the fast one: a CPU-only host loading a model, or
generating at one token per second, must not look dead to an intermediate proxy.
"""

_QUEUE_SENTINEL = object()


class PreparedTurn:
    """A validated turn, ready to stream.

    Constructed by :meth:`ChatService.prepare`. Holding this object means every
    precondition has already passed, so :meth:`stream` can start writing bytes
    immediately.
    """

    def __init__(
        self,
        *,
        state: AppState,
        provider: Provider,
        model_ref: str,
        chat_id: str,
        parent_id: str | None,
        depth: int,
        content: str,
        request: ChatRequest,
        citations: Sequence[RetrievedChunk] = (),
    ) -> None:
        """Store everything the stream needs; perform no I/O."""
        self._state = state
        self._provider = provider
        self._model_ref = model_ref
        self._chat_id = chat_id
        self._parent_id = parent_id
        self._depth = depth
        self._content = content
        self._request = request
        self._citations = citations
        self.user_message_id = new_ulid()
        self.assistant_message_id = new_ulid()

    async def stream(self) -> AsyncIterator[bytes]:
        """Run the turn, yielding complete SSE frames.

        Yields:
            Frames ready to write to the socket, per the event protocol in
            docs/design/03-http-api.md.
        """
        state = self._state
        provider_id = self._provider.provider_id
        model_key = self._request.model

        # The rows and the upstream request start together. The client already has the
        # message id, so nothing downstream is waiting on the insert (ADR-0005).
        insert = asyncio.create_task(self._insert_turn())

        writer = StreamWriter(
            state, chat_id=self._chat_id, message_id=self.assistant_message_id
        )
        timer = MonotonicTimer()

        yield encode_event(
            "start",
            {
                "message_id": self.assistant_message_id,
                "user_message_id": self.user_message_id,
                "parent_id": self._parent_id,
                "model_ref": self._model_ref,
            },
        )
        for citation in self._citations:
            yield encode_event(
                "citation",
                {
                    "chunk_id": citation.chunk_id,
                    "document_id": citation.document_id,
                    "locator": citation.locator,
                },
            )

        status = "error"
        finish_reason = "error"
        ttft_ms: float | None = None
        usage: Usage | None = None
        reasoning_parts: list[str] = []

        try:
            async for event in _pump(self._provider.stream_chat(self._request)):
                if event is None:
                    yield HEARTBEAT
                    continue

                if isinstance(event, TextDelta):
                    if ttft_ms is None:
                        ttft_ms = timer.elapsed_ms()
                        METRICS.ttft.labels(provider=provider_id, model=model_key).observe(
                            ttft_ms / 1_000.0
                        )
                    writer.append(event.text)
                    writer.maybe_flush()
                    yield encode_text_delta(event.text)

                elif isinstance(event, Status):
                    yield encode_event("status", {"phase": str(event.phase)})

                elif isinstance(event, ReasoningDelta):
                    reasoning_parts.append(event.text)
                    yield encode_event("reasoning", {"t": event.text})

                elif isinstance(event, ToolCallDelta):
                    yield encode_event(
                        "tool_call",
                        {"id": event.id, "name": event.name, "args": event.arguments_fragment},
                    )

                elif isinstance(event, Usage):
                    usage = event

                elif isinstance(event, Done):
                    finish_reason = event.finish_reason
                    status = "complete"

            yield encode_event(
                "usage", _usage_payload(usage, ttft_ms=ttft_ms, elapsed_ms=timer.elapsed_ms())
            )
            yield encode_event("done", {"finish_reason": finish_reason})
            _record_metrics(provider_id, model_key, usage, outcome="ok")

        except asyncio.CancelledError:
            # The client went away. The text the model already produced is still
            # theirs, so it is kept rather than discarded.
            status = "stopped"
            _record_metrics(provider_id, model_key, usage, outcome="stopped")
            raise

        except ProviderError as exc:
            status = "error"
            METRICS.provider_errors.labels(provider=provider_id, code=str(exc.code)).inc()
            _record_metrics(provider_id, model_key, usage, outcome="error")
            _log.warning(
                "provider failed mid-turn",
                extra={"provider": provider_id, "code": str(exc.code), "detail": exc.message},
            )
            yield encode_event("error", exc.to_payload()["error"])
            yield encode_event("done", {"finish_reason": "error"})

        except VeloxError as exc:
            status = "error"
            _record_metrics(provider_id, model_key, usage, outcome="error")
            yield encode_event("error", exc.to_payload()["error"])
            yield encode_event("done", {"finish_reason": "error"})

        finally:
            # Persistence runs in its own task and is only *awaited* here. If the
            # client has gone away this coroutine is being cancelled, and any database
            # work done inline would be cancelled with it — including the session's
            # own rollback, which leaks the connection and eventually exhausts the
            # pool. Shielding lets the write finish on its own schedule while this
            # request unwinds, which is also what ADR-0005 promises: an abandoned
            # stream still keeps what the model produced.
            finalizer = state.spawn(
                _finalize(
                    insert,
                    writer,
                    status=status,
                    reasoning="".join(reasoning_parts) or None,
                    model_ref=self._model_ref,
                    usage=usage,
                    is_local=self._provider.is_local,
                    ttft_ms=ttft_ms,
                    elapsed_ms=timer.elapsed_ms(),
                )
            )
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(finalizer)

    async def _insert_turn(self) -> None:
        """Insert the user turn and the empty assistant row."""
        async with self._state.db.write() as session:
            repository = ChatRepository(session)
            await repository.append_message(
                chat_id=self._chat_id,
                parent_id=self._parent_id,
                role="user",
                content=self._content,
                message_id=self.user_message_id,
                depth=self._depth,
            )
            await repository.append_message(
                chat_id=self._chat_id,
                parent_id=self.user_message_id,
                role="assistant",
                content="",
                status="streaming",
                model_ref=self._model_ref,
                message_id=self.assistant_message_id,
                depth=self._depth + 1,
            )


class ChatService:
    """Runs completion turns against configured providers.

    Args:
        state: Application state, for the database, providers and background tasks.
    """

    def __init__(self, state: AppState) -> None:
        """Bind the service to the application state."""
        self._state = state

    async def prepare(
        self,
        *,
        chat_id: str,
        user_id: str,
        content: str,
        model_ref: str,
        parent_id: str | None = None,
        params: SamplingParams | None = None,
        system_prompt: str | None = None,
        knowledge_ids: Sequence[str] = (),
    ) -> PreparedTurn:
        """Validate a turn and gather everything needed to run it.

        Args:
            chat_id: Conversation to append to.
            user_id: Owner, checked against the conversation.
            content: The user's message.
            model_ref: ``"provider_id:model_key"``.
            parent_id: Branch point. Defaults to the conversation's active leaf, which
                is the ordinary "continue the conversation" case.
            params: Per-turn sampling overrides.
            system_prompt: System message for this turn.
            knowledge_ids: Collection ids to retrieve context from before the turn
                starts (the client resolves these from the chat's custom model, the
                same way it already resolves ``system_prompt``). Empty is the default
                and every-day case, and costs nothing beyond this check — no import,
                no query — so a chat with no RAG configured pays no part of the
                retrieval budget (docs/design/00-overview.md, TTFT budget).

        Returns:
            A :class:`PreparedTurn`.

        Raises:
            NotFoundError: If the conversation does not exist or is not the caller's.
            ModelNotFound: If the reference names no configured provider.
        """
        resolved = await self._state.providers.resolve(model_ref)
        # Saved per-model parameters are cached in-process, the absence of any
        # included, so this costs a dictionary lookup on every turn after the first.
        saved = await self._state.model_params.get(user_id, model_ref)

        async with self._state.db.session() as session:
            repository = ChatRepository(session)
            chat = await repository.get(chat_id, user_id=user_id)
            if chat is None:
                raise NotFoundError("No such conversation.")
            branch_from = parent_id or chat.active_leaf_id
            path = await repository.load_active_path(chat_id, leaf_id=branch_from)

        capabilities = await self._capabilities_of(resolved.provider, resolved.model_key)
        messages = build_messages(
            path,
            system_prompt=system_prompt,
            context_window=capabilities.context_window if capabilities else None,
        )

        citations: list[RetrievedChunk] = []
        if knowledge_ids:
            citations = await self._retrieve(knowledge_ids, content)
            if citations:
                messages.insert(
                    0, ChatMessage(role="system", content=_context_block(citations))
                )

        messages.append(ChatMessage(role="user", content=content))

        return PreparedTurn(
            state=self._state,
            provider=resolved.provider,
            model_ref=model_ref,
            chat_id=chat_id,
            parent_id=branch_from,
            depth=(path[-1].depth + 1) if path else 0,
            content=content,
            request=ChatRequest(
                model=resolved.model_key,
                messages=tuple(messages),
                params=merge_params(saved, params or SamplingParams()),
            ),
            citations=citations,
        )

    async def _retrieve(
        self, knowledge_ids: Sequence[str], query: str, *, k_per_collection: int = 4
    ) -> list[RetrievedChunk]:
        """Fetch the best chunks across every knowledge collection attached to the turn.

        Only reached when ``knowledge_ids`` is non-empty, so ``rag/`` is imported here
        and nowhere on the default chat path (import-cost rule,
        docs/design/01-repo-layout.md).
        """
        from velox_ui.db.repositories.rag import CollectionRepository, to_collection_summary
        from velox_ui.rag.retrieve import retrieve

        hits: list[RetrievedChunk] = []
        async with self._state.db.session() as session:
            collections = [
                to_collection_summary(row)
                for collection_id in knowledge_ids
                if (row := await CollectionRepository(session).get(collection_id)) is not None
            ]
        for summary in collections:
            hits.extend(
                await retrieve(self._state, collection=summary, query=query, k=k_per_collection)
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: max(k_per_collection, 8)]

    @staticmethod
    async def _capabilities_of(provider: Provider, model_key: str) -> Capabilities | None:
        """Probe capabilities, tolerating a backend that cannot answer.

        A host that is slow or offline must not prevent the turn from being attempted:
        the request goes out without a context budget and the backend reports an
        overflow itself if there is one. Guessing a window here and silently
        truncating someone's conversation would be worse than either.
        """
        try:
            return await provider.capabilities(model_key)
        except Exception:
            return None


async def _finalize(
    insert: asyncio.Task[None],
    writer: StreamWriter,
    *,
    status: str,
    reasoning: str | None,
    model_ref: str,
    usage: Usage | None,
    is_local: bool,
    ttft_ms: float | None,
    elapsed_ms: float,
) -> None:
    """Complete the turn's persistence, independently of the request's lifetime."""
    await insert
    await writer.finalize(
        status=status,
        reasoning=reasoning,
        model_ref=model_ref,
        tokens_in=usage.tokens_in if usage else None,
        tokens_out=usage.tokens_out if usage else None,
        cost_micros=0 if is_local else None,
        timings=_timings(usage, ttft_ms=ttft_ms, elapsed_ms=elapsed_ms),
    )


def _context_block(citations: Sequence[RetrievedChunk]) -> str:
    """Render retrieved chunks as a system message the provider sees before the user turn."""
    parts = [
        f"[{index}] {citation.document_title}\n{citation.content}"
        for index, citation in enumerate(citations, start=1)
    ]
    return (
        "Use the following retrieved context to answer the user's question. "
        "Cite it by its bracketed number when you rely on it; ignore it if it is "
        "not relevant.\n\n" + "\n\n".join(parts)
    )


async def _pump(source: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent | None]:
    """Drain a provider stream into a queue, yielding ``None`` as a heartbeat tick.

    The queue exists so the heartbeat timeout applies to ``queue.get()`` and not to the
    provider's own read: timing out a read would cancel the generation.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=64)

    async def drain() -> None:
        try:
            async for event in source:
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await queue.put(exc)
        else:
            await queue.put(_QUEUE_SENTINEL)

    task = asyncio.create_task(drain())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_S)
            except TimeoutError:
                yield None
                continue
            if item is _QUEUE_SENTINEL:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        task.cancel()


def _usage_payload(
    usage: Usage | None, *, ttft_ms: float | None, elapsed_ms: float
) -> dict[str, Any]:
    """Build the ``usage`` SSE payload."""
    return {
        "tokens_in": usage.tokens_in if usage else 0,
        "tokens_out": usage.tokens_out if usage else 0,
        "cost_micros": usage.cost_micros if usage else 0,
        "ttft_ms": round(ttft_ms, 2) if ttft_ms is not None else None,
        "tok_per_s": round(usage.tokens_per_second, 2)
        if usage and usage.tokens_per_second
        else None,
        "prompt_eval_ms": usage.prompt_eval_ms if usage else None,
        "eval_ms": usage.eval_ms if usage else None,
        "duration_ms": round(elapsed_ms, 2),
    }


def _timings(
    usage: Usage | None, *, ttft_ms: float | None, elapsed_ms: float
) -> dict[str, Any]:
    """Build the ``message.timings`` column value."""
    return {
        "ttft_ms": round(ttft_ms, 2) if ttft_ms is not None else None,
        "duration_ms": round(elapsed_ms, 2),
        "tok_per_s": usage.tokens_per_second if usage else None,
        "prompt_eval_ms": usage.prompt_eval_ms if usage else None,
        "eval_ms": usage.eval_ms if usage else None,
    }


def _record_metrics(
    provider_id: str, model_key: str, usage: Usage | None, *, outcome: str
) -> None:
    """Record per-provider, per-model counters for a finished turn."""
    METRICS.completions.labels(provider=provider_id, model=model_key, outcome=outcome).inc()
    if usage is None:
        return
    METRICS.tokens.labels(provider=provider_id, model=model_key, direction="input").inc(
        usage.tokens_in
    )
    METRICS.tokens.labels(provider=provider_id, model=model_key, direction="output").inc(
        usage.tokens_out
    )
    if usage.tokens_per_second:
        METRICS.generation_rate.labels(provider=provider_id, model=model_key).observe(
            usage.tokens_per_second
        )
