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

from collections.abc import Sequence

from velox_ui.db.repositories.chats import MessageNode
from velox_ui.providers.base import ChatMessage

__all__ = ["CHARS_PER_TOKEN", "build_messages", "estimate_tokens"]

# English prose runs around four characters per token across the common BPE
# vocabularies. Code and non-Latin scripts run denser, which is why the reserve below
# is generous rather than exact.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Return a conservative token estimate for a string."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def build_messages(
    path: Sequence[MessageNode],
    *,
    system_prompt: str | None = None,
    context_window: int | None = None,
    reserve_for_output: int = 1024,
) -> list[ChatMessage]:
    """Build the provider message list for the next turn.

    Args:
        path: The active branch, root first, as stored.
        system_prompt: Prepended as a system message when given.
        context_window: The model's real context window, as probed from the backend.
            ``None`` means unknown, in which case nothing is dropped — guessing a
            window and silently truncating someone's conversation is worse than
            letting the backend report the overflow itself.
        reserve_for_output: Tokens held back for the model's reply.

    Returns:
        Messages in provider order, oldest first, with the system prompt first.

    Older turns are dropped from the front, never the middle: a conversation missing
    its middle reads as a model that has lost the plot, while one missing its opening
    reads as a conversation that has simply been going on for a while.
    """
    messages = [
        ChatMessage(role=node.role, content=node.content)  # type: ignore[arg-type]
        for node in path
        if node.role in ("user", "assistant", "system") and node.content
    ]

    if context_window is None:
        return _with_system(messages, system_prompt)

    budget = context_window - reserve_for_output
    if system_prompt:
        budget -= estimate_tokens(system_prompt)

    kept: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        text = message.content if isinstance(message.content, str) else ""
        cost = estimate_tokens(text)
        if used + cost > budget and kept:
            break
        used += cost
        kept.append(message)
    kept.reverse()

    return _with_system(kept, system_prompt)


def _with_system(messages: list[ChatMessage], system_prompt: str | None) -> list[ChatMessage]:
    """Prepend the system prompt, if there is one."""
    if not system_prompt:
        return messages
    return [ChatMessage(role="system", content=system_prompt), *messages]
