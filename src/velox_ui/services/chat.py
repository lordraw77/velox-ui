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
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

import msgspec

from velox_ui.api.sse import HEARTBEAT, encode_event, encode_text_delta
from velox_ui.clock import MonotonicTimer
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.errors import NotFoundError, VeloxError
from velox_ui.ids import new_ulid
from velox_ui.mcp.client import McpError
from velox_ui.mcp.schema_translate import render_tool_result, split_qualified_name, to_tool_spec
from velox_ui.metrics import METRICS
from velox_ui.plugins.errors import PluginDisabledError, PluginUpstreamError
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
    ToolCall,
    ToolCallDelta,
    ToolSpec,
    ToolSupport,
    Usage,
)
from velox_ui.providers.errors import ProviderError
from velox_ui.providers.tools.emulated import (
    build_tool_prompt,
    parse_tool_call,
    strip_tool_call,
)
from velox_ui.services.context import build_messages
from velox_ui.services.model_params import merge_params
from velox_ui.services.persistence import StreamWriter
from velox_ui.state import AppState

if TYPE_CHECKING:  # pragma: no cover - typing only; keeps rag/ off the cold path.
    from velox_ui.mcp.manager import ServerTool
    from velox_ui.rag.retrieve import RetrievedChunk

__all__ = ["HEARTBEAT_INTERVAL_S", "ChatService", "PreparedTurn"]

_log = logging.getLogger("velox.chat")

HEARTBEAT_INTERVAL_S = 15.0
"""How long a stream may be silent before a keep-alive comment is sent.

Tuned for the slow case, not the fast one: a CPU-only host loading a model, or
generating at one token per second, must not look dead to an intermediate proxy.
"""

_QUEUE_SENTINEL = object()

MAX_TOOL_ITERATIONS = 4
"""Hard cap on tool-call round trips within one turn.

Without a cap, a model stuck calling the same tool over and over would turn one HTTP
request into an unbounded number of MCP calls and provider round trips. Four rounds
covers every realistic "look something up, then answer" pattern; a model that still
wants another tool after that gets its last tool result and is left to answer with
what it has, same as a model given no tools that ran out of context.
"""

_APPROVAL_TIMEOUT_S = 300.0


class _BuiltinTool(msgspec.Struct, frozen=True):
    """A builtin (plugin-backed, non-MCP) tool offered for one turn.

    Distinguished from :class:`~velox_ui.mcp.manager.ServerTool` in ``tool_index``
    by ``isinstance`` in :meth:`PreparedTurn._run_tool_call`: a builtin tool has no
    owning server, no approval gate, and dispatches through
    ``AppState.plugins.call_tool`` instead of ``AppState.mcp.call_tool``.
    """

    name: str


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
        user_id: str,
        parent_id: str | None,
        depth: int,
        content: str,
        request: ChatRequest,
        citations: Sequence[RetrievedChunk] = (),
        tool_index: dict[str, ServerTool | _BuiltinTool] | None = None,
        native_tools: bool = False,
    ) -> None:
        """Store everything the stream needs; perform no I/O.

        Args:
            state: Application state.
            provider: The resolved backend for this turn.
            model_ref: ``"provider_id:model_key"``.
            chat_id: Conversation this turn appends to.
            user_id: The turn's owner, needed mid-stream to execute an MCP tool call
                and to check approval on its behalf.
            parent_id: Branch point for the new messages.
            depth: Tree depth of the user message about to be inserted.
            content: The user's message text.
            request: The provider request built by :meth:`ChatService.prepare`.
            citations: RAG hits to announce before generation starts.
            tool_index: Every offered tool, keyed by its provider-facing name — an
                MCP tool's qualified ``"<server>__<tool>"`` name, or a builtin
                plugin tool's flat name (``"web_search"``, never qualified, so the
                two never collide) — so a tool call the model emits can be traced
                back to how to execute it. Empty when the turn offers no tools —
                the common case, and the one this costs nothing beyond an
                empty-dict membership check for.
            native_tools: Whether tools were attached to ``request.tools`` for the
                provider's own tool-calling support. When ``False`` but ``tool_index``
                is non-empty, tools were instead described in the prompt
                (``providers/tools/emulated.py``) and a tool call is looked for by
                parsing the model's text after each round finishes.
        """
        self._state = state
        self._provider = provider
        self._model_ref = model_ref
        self._chat_id = chat_id
        self._user_id = user_id
        self._parent_id = parent_id
        self._depth = depth
        self._content = content
        self._request = request
        self._citations = citations
        self._tool_index = tool_index or {}
        self._native_tools = native_tools
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
        tool_trace: list[dict[str, Any]] = []
        messages = list(self._request.messages)

        try:
            for iteration in range(MAX_TOOL_ITERATIONS):
                request = (
                    msgspec.structs.replace(self._request, messages=tuple(messages))
                    if iteration > 0
                    else self._request
                )
                round_text_parts: list[str] = []
                native_calls: dict[str, _CallBuffer] = {}
                round_finish = "stop"

                async for event in _pump(self._provider.stream_chat(request)):
                    if event is None:
                        yield HEARTBEAT
                        continue

                    if isinstance(event, TextDelta):
                        if ttft_ms is None:
                            ttft_ms = timer.elapsed_ms()
                            METRICS.ttft.labels(provider=provider_id, model=model_key).observe(
                                ttft_ms / 1_000.0
                            )
                        round_text_parts.append(event.text)
                        writer.append(event.text)
                        writer.maybe_flush()
                        yield encode_text_delta(event.text)

                    elif isinstance(event, Status):
                        yield encode_event("status", {"phase": str(event.phase)})

                    elif isinstance(event, ReasoningDelta):
                        reasoning_parts.append(event.text)
                        yield encode_event("reasoning", {"t": event.text})

                    elif isinstance(event, ToolCallDelta):
                        buffer = native_calls.setdefault(
                            event.id, _CallBuffer(id=event.id, name=event.name)
                        )
                        buffer.arguments += event.arguments_fragment

                    elif isinstance(event, Usage):
                        usage = _merge_usage(usage, event)

                    elif isinstance(event, Done):
                        round_finish = event.finish_reason
                        status = "complete"

                finish_reason = round_finish
                round_text = "".join(round_text_parts)
                calls = self._detect_tool_calls(round_finish, native_calls, round_text)
                if not calls or iteration == MAX_TOOL_ITERATIONS - 1:
                    break

                messages.append(
                    _assistant_tool_call_message(calls, round_text, native=self._native_tools)
                )
                for call in calls:
                    async for frame in self._run_tool_call(call, messages, tool_trace):
                        yield frame

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
                    meta={"tool_trace": tool_trace} if tool_trace else None,
                )
            )
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(finalizer)

    def _detect_tool_calls(
        self,
        finish_reason: str,
        native_calls: dict[str, _CallBuffer],
        round_text: str,
    ) -> list[ToolCall]:
        """Resolve one round's tool calls, native first, then the emulated text parse.

        Returns an empty list when the turn offers no tools at all — the common case,
        checked first so a chat with no MCP servers attached never runs the emulated
        parser over its ordinary replies.
        """
        if not self._tool_index:
            return []
        if self._native_tools:
            if finish_reason != "tool_calls" or not native_calls:
                return []
            return [
                ToolCall(id=buf.id, name=buf.name, arguments=buf.arguments or "{}")
                for buf in native_calls.values()
            ]
        call = parse_tool_call(round_text, call_id=new_ulid())
        return [call] if call is not None else []

    async def _run_tool_call(
        self, call: ToolCall, messages: list[ChatMessage], tool_trace: list[dict[str, Any]]
    ) -> AsyncIterator[bytes]:
        """Execute one tool call: approval gate, execution, SSE events, context append.

        Appends the tool's result to ``messages`` in place so the next generation
        round sees it, and records a trace entry consumed by :meth:`stream` for the
        persisted message's ``meta.tool_trace``.
        """
        tool_entry = self._tool_index.get(call.name)
        try:
            arguments = json.loads(call.arguments) if call.arguments else {}
            if not isinstance(arguments, dict):
                arguments = {}
        except json.JSONDecodeError:
            arguments = {}

        if isinstance(tool_entry, _BuiltinTool):
            # No approval gate (a scope decision, not an oversight): a builtin
            # plugin tool runs immediately, unlike an MCP server's, which can be
            # configured to require one.
            yield encode_event(
                "tool_call",
                {"id": call.id, "name": call.name, "args": arguments, "approval": "auto"},
            )
            try:
                content = await self._state.plugins.call_tool(tool_entry.name, arguments)
                ok = True
            except (PluginDisabledError, PluginUpstreamError) as exc:
                ok = False
                content = f"Tool call failed: {exc.message}"
        elif tool_entry is None:
            ok = False
            content = f"Unknown tool '{call.name}'."
            yield encode_event(
                "tool_call", {"id": call.id, "name": call.name, "args": arguments}
            )
        else:
            server_tool = tool_entry
            split = split_qualified_name(call.name)
            raw_name = split[1] if split is not None else call.name
            decision = self._state.mcp.gate(
                server_id=server_tool.server_id,
                approval=server_tool.approval,
                tool_name=raw_name,
            )
            approval_field = "required" if decision.requires_wait else "auto"
            yield encode_event(
                "tool_call",
                {
                    "id": call.id,
                    "name": call.name,
                    "args": arguments,
                    "approval": approval_field,
                },
            )
            approved = decision.approved
            if decision.requires_wait:
                self._state.mcp.register_pending(
                    call.id, server_id=server_tool.server_id, tool_name=raw_name
                )
                yield encode_event("status", {"phase": "tool_wait"})
                approved = await self._state.mcp.wait_for_approval(
                    call.id, timeout_s=_APPROVAL_TIMEOUT_S
                )

            if not approved:
                ok = False
                content = "The user did not approve this tool call."
            else:
                try:
                    result = await self._state.mcp.call_tool(
                        server_tool.server_id, raw_name, arguments, user_id=self._user_id
                    )
                    ok = not result.is_error
                    content = render_tool_result(result)
                except McpError as exc:
                    ok = False
                    content = f"Tool call failed: {exc.message}"

        yield encode_event("tool_result", {"id": call.id, "ok": ok, "content": content})
        tool_trace.append(
            {"id": call.id, "name": call.name, "args": arguments, "ok": ok, "content": content}
        )
        _append_tool_result(messages, call, content, native=self._native_tools)

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
        tool_server_ids: Sequence[str] = (),
        web_tools: bool = False,
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
            tool_server_ids: Enabled MCP server ids to offer tools from (resolved by
                the client from the chat's custom model, same pattern as
                ``knowledge_ids``). Empty is the default and costs one dictionary
                check on the completion path, same reasoning as ``knowledge_ids``.
            web_tools: Whether to offer the enabled builtin ``"tools"`` plugin's
                tools (web search and browsing, ADR-0014), resolved by the client
                from the chat's custom model the same way. ``False`` is the
                default; when set, one plugin-registry lookup decides whether a
                ``"tools"`` plugin is configured at all.

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

        tool_index: dict[str, ServerTool | _BuiltinTool] = {}
        request_tools: tuple[ToolSpec, ...] | None = None
        native_tools = False
        specs: list[ToolSpec] = []
        if tool_server_ids:
            server_tools = await self._state.mcp.tools_for_servers(
                tool_server_ids, user_id=user_id
            )
            for server_tool in server_tools:
                spec = to_tool_spec(server_tool.tool, server_name=server_tool.server_name)
                specs.append(spec)
                tool_index[spec.name] = server_tool
        if web_tools:
            for tool_plugin in await self._state.plugins.tool_plugins():
                for tool in tool_plugin.tools():
                    spec = ToolSpec(
                        name=tool.name, description=tool.description, parameters=tool.parameters
                    )
                    specs.append(spec)
                    tool_index[spec.name] = _BuiltinTool(name=tool.name)
        if specs:
            native_tools = capabilities is not None and capabilities.tools == ToolSupport.NATIVE
            if native_tools:
                request_tools = tuple(specs)
            else:
                messages.append(ChatMessage(role="system", content=build_tool_prompt(specs)))

        messages.append(ChatMessage(role="user", content=content))

        return PreparedTurn(
            state=self._state,
            provider=resolved.provider,
            model_ref=model_ref,
            chat_id=chat_id,
            user_id=user_id,
            parent_id=branch_from,
            depth=(path[-1].depth + 1) if path else 0,
            content=content,
            request=ChatRequest(
                model=resolved.model_key,
                messages=tuple(messages),
                params=merge_params(saved, params or SamplingParams()),
                tools=request_tools,
            ),
            citations=citations,
            tool_index=tool_index,
            native_tools=native_tools,
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
    meta: dict[str, Any] | None = None,
) -> None:
    """Complete the turn's persistence, independently of the request's lifetime."""
    await insert
    await writer.finalize(
        status=status,
        reasoning=reasoning,
        model_ref=model_ref,
        meta=meta,
        tokens_in=usage.tokens_in if usage else None,
        tokens_out=usage.tokens_out if usage else None,
        cost_micros=0 if is_local else None,
        timings=_timings(usage, ttft_ms=ttft_ms, elapsed_ms=elapsed_ms),
    )


class _CallBuffer:
    """Accumulates one native tool call's argument JSON across ``ToolCallDelta`` fragments."""

    __slots__ = ("arguments", "id", "name")

    def __init__(self, *, id: str, name: str) -> None:
        """Start an empty buffer for one call id."""
        self.id = id
        self.name = name
        self.arguments = ""


def _assistant_tool_call_message(
    calls: list[ToolCall], round_text: str, *, native: bool
) -> ChatMessage:
    """Build the assistant-turn message that preceded this round's tool call(s).

    Native tool calling carries the calls as structured ``tool_calls``, the shape
    every OpenAI- and Anthropic-style multi-turn tool loop expects to see echoed back.
    Emulated tool calling has no such field on the wire, so the model's own text
    (with the ``<tool_call>`` block stripped, since the result is about to answer it)
    stands in for what it "said" before the call.
    """
    if native:
        return ChatMessage(role="assistant", content="", tool_calls=tuple(calls))
    return ChatMessage(role="assistant", content=strip_tool_call(round_text))


def _append_tool_result(
    messages: list[ChatMessage], call: ToolCall, content: str, *, native: bool
) -> None:
    """Append one tool's result to the running message list, for the next round.

    Native tool calling gets a ``role="tool"`` message tied to the call by
    ``tool_call_id`` (``providers/base.py``); emulated models were never taught that
    role exists, so the result instead arrives as an ordinary ``user`` message asking
    the model to continue with it.
    """
    if native:
        messages.append(
            ChatMessage(role="tool", content=content, tool_call_id=call.id, name=call.name)
        )
    else:
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    f"Tool '{call.name}' result:\n{content}\n\n"
                    "Continue your answer using this result."
                ),
            )
        )


def _merge_usage(existing: Usage | None, new: Usage) -> Usage:
    """Sum token accounting across tool-loop rounds; each round is one backend call."""
    if existing is None:
        return new
    return Usage(
        tokens_in=existing.tokens_in + new.tokens_in,
        tokens_out=existing.tokens_out + new.tokens_out,
        ttft_ms=existing.ttft_ms,
        tokens_per_second=new.tokens_per_second or existing.tokens_per_second,
        prompt_eval_ms=(existing.prompt_eval_ms or 0) + (new.prompt_eval_ms or 0) or None,
        eval_ms=(existing.eval_ms or 0) + (new.eval_ms or 0) or None,
        cost_micros=existing.cost_micros + new.cost_micros,
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
