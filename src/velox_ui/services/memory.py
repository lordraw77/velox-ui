"""A conversation's memory: what the model keeps of the part it no longer sees.

A long chat outgrows its model's window, and the only thing ``context.build_messages``
can do about that is drop the oldest turns. Compaction does better by asking the model
to write down what the rest of the conversation needs from them -- decisions, facts,
open questions -- and storing that on the chat. From then on the notes are sent in
place of the messages they cover.

Nothing is deleted. The messages stay in the tree and on screen; the chat records only
the last one the notes stand in for (``Chat.memory_upto_id``), so compaction can be
undone by clearing the memory, and a branch made before that message is unaffected.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

import msgspec

from velox_ui.db.repositories.chats import ChatRepository, MessageNode
from velox_ui.errors import ConflictError, NotFoundError, ValidationError
from velox_ui.providers.base import ChatMessage, ChatRequest, SamplingParams, TextDelta
from velox_ui.services.context import estimate_tokens
from velox_ui.state import AppState

__all__ = ["CompactionResult", "MemoryService"]

# The latest exchange stays verbatim: a model answering the next message needs the
# exact words it was just given, not a paraphrase of them.
DEFAULT_KEEP_LAST = 2

_INSTRUCTIONS = (
    "You maintain the memory of a conversation between a user and an assistant. "
    "Write notes that let the assistant continue the conversation as if it had read "
    "all of it. Keep: what the user is trying to achieve, decisions made and why, "
    "facts, names, numbers, file paths and code identifiers the user gave, "
    "preferences and constraints, what has been done, and what is still open. "
    "Drop greetings, filler and anything already superseded. Use short bullet points "
    "under plain headings, in the language of the conversation. Output only the notes."
)


class CompactionResult(msgspec.Struct, frozen=True):
    """What a compaction produced."""

    memory: str
    memory_upto_id: str
    summarized: int
    """Messages now covered by the memory, counting those it already covered."""
    omitted: int
    """Messages too old to fit the model's window while summarizing; their content is
    not in the notes unless an earlier memory already held it."""


class MemoryService:
    """Writes and rewrites a conversation's memory.

    Args:
        state: Application state, for the database and providers.
    """

    def __init__(self, state: AppState) -> None:
        """Bind the service to the application state."""
        self._state = state

    async def compact(
        self,
        *,
        chat_id: str,
        user_id: str,
        model_ref: str,
        keep_last: int = DEFAULT_KEEP_LAST,
    ) -> CompactionResult:
        """Summarize the conversation so far into the chat's memory.

        The model is given the existing memory (if any) and the messages after it, up
        to but not including the last ``keep_last``, and returns notes that replace
        both. The result is stored with the anchor moved forward.

        Args:
            chat_id: The conversation.
            user_id: Owner, checked against the conversation.
            model_ref: ``"provider_id:model_key"`` that writes the notes.
            keep_last: How many of the newest messages to leave out of the notes.

        Raises:
            NotFoundError: If the conversation does not exist or is not the caller's.
            ConflictError: If a turn is running in it, since the path is still moving.
            ValidationError: If there is nothing new to summarize.
            ModelNotFound: If the reference names no configured provider.
        """
        if self._state.turns.active(chat_id) is not None:
            raise ConflictError("Wait for the running reply to finish before compacting.")
        resolved = await self._state.providers.resolve(model_ref)

        async with self._state.db.session() as session:
            repository = ChatRepository(session)
            chat = await repository.get(chat_id, user_id=user_id)
            if chat is None:
                raise NotFoundError("No such conversation.")
            path = await repository.load_active_path(chat_id)
            previous, anchor = chat.memory, chat.memory_upto_id

        usable = [node for node in path if node.role in ("user", "assistant") and node.content]
        start = 0
        if previous and anchor:
            ids = [node.id for node in path]
            if anchor in ids:
                start = ids.index(anchor) + 1
            else:
                previous = None
        fresh = [node for node in path[start:] if node in usable]
        fresh = fresh[: max(0, len(fresh) - max(0, keep_last))]
        if not fresh:
            raise ValidationError("There is nothing new to put in the memory yet.")

        window = None
        with contextlib.suppress(Exception):  # an unreachable probe just means no budget
            window = (await resolved.provider.capabilities(resolved.model_key)).context_window
        transcript, omitted = _transcript(fresh, previous=previous, window=window)

        text = ""
        async for event in resolved.provider.stream_chat(
            ChatRequest(
                model=resolved.model_key,
                messages=(
                    ChatMessage(role="system", content=_INSTRUCTIONS),
                    ChatMessage(role="user", content=transcript),
                ),
                params=SamplingParams(temperature=0.2),
            )
        ):
            if isinstance(event, TextDelta):
                text += event.text
        notes = text.strip()
        if not notes:
            raise ValidationError("The model returned no notes. Try another model.")

        upto_id = fresh[-1].id
        async with self._state.db.write() as session:
            stored = await ChatRepository(session).set_memory(
                chat_id, user_id=user_id, memory=notes, upto_id=upto_id
            )
        if not stored:
            raise NotFoundError("No such conversation.")
        covered = len([node for node in path[: path.index(fresh[-1]) + 1] if node in usable])
        return CompactionResult(
            memory=notes, memory_upto_id=upto_id, summarized=covered, omitted=omitted
        )


def _transcript(
    nodes: Sequence[MessageNode], *, previous: str | None, window: int | None
) -> tuple[str, int]:
    """Render the summarization request, newest messages first when space is short."""
    head = ""
    if previous:
        head = f"Current notes (revise them, do not repeat them verbatim):\n{previous}\n\n"
    head += "New messages to fold into the notes:\n\n"
    lines = [f"{node.role.upper()}: {node.content}" for node in nodes]

    if window is None:
        return head + "\n\n".join(lines), 0
    budget = window - estimate_tokens(_INSTRUCTIONS) - estimate_tokens(head) - 1024
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = estimate_tokens(line)
        if used + cost > budget and kept:
            break
        used += cost
        kept.append(line)
    kept.reverse()
    return head + "\n\n".join(kept), len(lines) - len(kept)
