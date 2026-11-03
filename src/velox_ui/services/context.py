"""Turning a stored conversation into a provider request.

The message tree is the source of truth; a provider wants a flat list. This module
walks the active branch and produces that list, applying a token budget so a long
conversation degrades by dropping its oldest turns rather than by the backend
rejecting the whole request.

Token counting here is an estimate, deliberately. Every model tokenises differently and
the only exact count comes from the backend itself, after the fact; querying a
tokeniser per turn would add a round trip to the hot path to buy precision that only
matters at the very edge of the window. The estimate is conservative — it overcounts
rather than under — so the budget errs toward sending less than the window allows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import msgspec

from velox_ui.db.repositories.chats import MessageNode
from velox_ui.providers.base import ChatMessage, ContentPart

__all__ = [
    "CHARS_PER_TOKEN",
    "ContextUsage",
    "attachment_ids",
    "build_messages",
    "estimate_tokens",
    "measure_context",
    "memory_system_message",
]

# English prose runs around four characters per token across the common BPE
# vocabularies. Code and non-Latin scripts run denser, which is why the reserve below
# is generous rather than exact.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Return a conservative token estimate for a string."""
    return max(1, len(text) // CHARS_PER_TOKEN)


# A picture costs a roughly fixed number of tokens whatever its bytes, and the real
# figure depends on the model. This keeps the budget honest without pretending to know.
IMAGE_TOKEN_ESTIMATE = 800


def attachment_ids(meta: Mapping[str, object] | None) -> list[str]:
    """Return the file ids of the images attached to a message, from its ``meta``."""
    entries = (meta or {}).get("attachments")
    if not isinstance(entries, list):
        return []
    return [entry["id"] for entry in entries if isinstance(entry, dict) and "id" in entry]


def with_images(text: str, images: Sequence[ContentPart]) -> str | tuple[ContentPart, ...]:
    """Combine a message's text with its images into one provider message body."""
    if not images:
        return text
    parts = (ContentPart(kind="text", text=text),) if text else ()
    return (*parts, *images)


def message_cost(message: ChatMessage) -> int:
    """Estimate the tokens one provider message takes."""
    if isinstance(message.content, str):
        return estimate_tokens(message.content)
    return sum(
        estimate_tokens(part.text or "") if part.kind == "text" else IMAGE_TOKEN_ESTIMATE
        for part in message.content
    )


def memory_system_message(memory: str) -> str:
    """Wrap a chat's memory as the system text that carries it to the model."""
    return (
        "Notes from the earlier part of this conversation, which is no longer shown "
        "in full. Treat them as established context:\n\n" + memory.strip()
    )


class ContextUsage(msgspec.Struct, frozen=True):
    """How much of the model's window the next turn will use, and what it leaves out.

    Every figure is an estimate (see the module docstring); ``window`` is the real
    figure the backend reported, or ``None`` when it did not say.
    """

    window: int | None
    reserve: int
    used: int
    system_tokens: int
    memory_tokens: int
    messages_total: int
    messages_in_context: int
    messages_summarized: int
    messages_dropped: int


def _eligible(path: Sequence[MessageNode]) -> list[MessageNode]:
    return [
        node
        for node in path
        if node.role in ("user", "assistant", "system")
        and (node.content or attachment_ids(node.meta))
    ]


def _after_memory(
    path: Sequence[MessageNode], memory: str | None, memory_upto_id: str | None
) -> tuple[Sequence[MessageNode], str | None, int]:
    """Apply a chat's memory to its path.

    Returns the part of the path the memory does not cover, the memory to send, and
    how many messages it replaced. A memory anchored to a message that is not on this
    branch describes a different branch, so it is left out entirely rather than sent
    next to a conversation it does not belong to.
    """
    if not memory or not memory.strip():
        return path, None, 0
    if memory_upto_id is None:
        return path, memory, 0
    for index, node in enumerate(path):
        if node.id == memory_upto_id:
            return path[index + 1 :], memory, len(_eligible(path[: index + 1]))
    return path, None, 0


def build_messages(
    path: Sequence[MessageNode],
    *,
    images: Mapping[str, ContentPart] | None = None,
    system_prompt: str | None = None,
    context_window: int | None = None,
    reserve_for_output: int = 1024,
    memory: str | None = None,
    memory_upto_id: str | None = None,
) -> list[ChatMessage]:
    """Build the provider message list for the next turn.

    Args:
        path: The active branch, root first, as stored.
        images: Image parts by file id, for the attachments of messages on ``path``.
            An id missing here -- a file deleted since -- is skipped, not an error.
        system_prompt: Prepended as a system message when given.
        context_window: The model's real context window, as probed from the backend.
            ``None`` means unknown, in which case nothing is dropped — guessing a
            window and silently truncating someone's conversation is worse than
            letting the backend report the overflow itself.
        reserve_for_output: Tokens held back for the model's reply.
        memory: The chat's memory notes. Sent as a system message after the system
            prompt, and counted against the budget.
        memory_upto_id: The last message the memory replaces. Messages up to and
            including it are not sent; they stay stored.

    Returns:
        Messages in provider order, oldest first, with the system prompt first.

    Older turns are dropped from the front, never the middle: a conversation missing
    its middle reads as a model that has lost the plot, while one missing its opening
    reads as a conversation that has simply been going on for a while.
    """
    kept, lead, _ = _plan(
        path,
        images=images,
        system_prompt=system_prompt,
        context_window=context_window,
        reserve_for_output=reserve_for_output,
        memory=memory,
        memory_upto_id=memory_upto_id,
    )
    return [*lead, *kept]


def measure_context(
    path: Sequence[MessageNode],
    *,
    system_prompt: str | None = None,
    context_window: int | None = None,
    reserve_for_output: int = 1024,
    memory: str | None = None,
    memory_upto_id: str | None = None,
) -> ContextUsage:
    """Report what :func:`build_messages` would send for the same arguments.

    Images are counted at their fixed estimate whether or not their bytes are loaded,
    so measuring never reads a file.
    """
    placeholder = {
        file_id: ContentPart(kind="image")
        for node in path
        for file_id in attachment_ids(node.meta)
    }
    kept, _, summarized = _plan(
        path,
        images=placeholder,
        system_prompt=system_prompt,
        context_window=context_window,
        reserve_for_output=reserve_for_output,
        memory=memory,
        memory_upto_id=memory_upto_id,
    )
    remaining, _, _ = _after_memory(path, memory, memory_upto_id)
    eligible = len(_eligible(remaining))
    system_tokens = estimate_tokens(system_prompt) if system_prompt else 0
    live_memory = _after_memory(path, memory, memory_upto_id)[1]
    memory_tokens = estimate_tokens(memory_system_message(live_memory)) if live_memory else 0
    return ContextUsage(
        window=context_window,
        reserve=reserve_for_output,
        used=system_tokens + memory_tokens + sum(message_cost(m) for m in kept),
        system_tokens=system_tokens,
        memory_tokens=memory_tokens,
        messages_total=len(_eligible(path)),
        messages_in_context=len(kept),
        messages_summarized=summarized,
        messages_dropped=eligible - len(kept),
    )


def _plan(
    path: Sequence[MessageNode],
    *,
    images: Mapping[str, ContentPart] | None,
    system_prompt: str | None,
    context_window: int | None,
    reserve_for_output: int,
    memory: str | None,
    memory_upto_id: str | None,
) -> tuple[list[ChatMessage], list[ChatMessage], int]:
    """Choose what to send.

    Returns the kept history, the leading system messages, and the count of messages
    the memory replaced.
    """
    remaining, live_memory, summarized = _after_memory(path, memory, memory_upto_id)
    known = images or {}
    messages = [
        ChatMessage(
            role=node.role,  # type: ignore[arg-type]
            content=with_images(
                node.content,
                [known[file_id] for file_id in attachment_ids(node.meta) if file_id in known],
            ),
        )
        for node in _eligible(remaining)
    ]

    lead: list[ChatMessage] = []
    if system_prompt:
        lead.append(ChatMessage(role="system", content=system_prompt))
    if live_memory:
        lead.append(ChatMessage(role="system", content=memory_system_message(live_memory)))

    if context_window is None:
        return messages, lead, summarized

    budget = context_window - reserve_for_output - sum(message_cost(m) for m in lead)
    kept: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        cost = message_cost(message)
        if used + cost > budget and kept:
            break
        used += cost
        kept.append(message)
    kept.reverse()
    return kept, lead, summarized
