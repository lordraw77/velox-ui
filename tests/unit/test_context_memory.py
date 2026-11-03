"""Chat memory in the context builder: what it replaces, what it costs, when it applies."""

from __future__ import annotations

from velox_ui.db.repositories.chats import MessageNode
from velox_ui.services.context import build_messages, measure_context


def _node(index: int, role: str, content: str) -> MessageNode:
    return MessageNode(
        id=f"m{index}",
        parent_id=f"m{index - 1}" if index else None,
        role=role,
        content=content,
        reasoning=None,
        status="complete",
        model_ref=None,
        depth=index,
        tokens_in=None,
        tokens_out=None,
        cost_micros=None,
        timings=None,
        error=None,
        meta=None,
        created_at=0,
        sibling_index=0,
        sibling_count=1,
    )


PATH = [
    _node(0, "user", "first question"),
    _node(1, "assistant", "first answer"),
    _node(2, "user", "second question"),
    _node(3, "assistant", "second answer"),
]


def test_memory_replaces_the_messages_it_covers() -> None:
    messages = build_messages(PATH, memory="user wants X", memory_upto_id="m1")
    assert [m.role for m in messages] == ["system", "user", "assistant"]
    assert "user wants X" in str(messages[0].content)
    assert messages[1].content == "second question"


def test_memory_without_an_anchor_covers_nothing() -> None:
    messages = build_messages(PATH, memory="always remember Y")
    assert len(messages) == 5
    assert "always remember Y" in str(messages[0].content)


def test_memory_anchored_off_this_branch_is_ignored() -> None:
    messages = build_messages(PATH, memory="other branch", memory_upto_id="elsewhere")
    assert [m.content for m in messages] == [n.content for n in PATH]


def test_system_prompt_comes_before_memory() -> None:
    messages = build_messages(
        PATH, system_prompt="be brief", memory="notes", memory_upto_id="m1"
    )
    assert messages[0].content == "be brief"
    assert "notes" in str(messages[1].content)


def test_memory_counts_against_the_budget() -> None:
    long_memory = "x" * 4000  # ~1000 tokens
    messages = build_messages(
        PATH, memory=long_memory, memory_upto_id="m0", context_window=1030, reserve_for_output=0
    )
    # Only the newest message still fits next to the notes.
    assert [m.role for m in messages] == ["system", "assistant"]


def test_measure_reports_summarized_and_dropped() -> None:
    usage = measure_context(PATH, memory="notes", memory_upto_id="m1")
    assert usage.messages_total == 4
    assert usage.messages_summarized == 2
    assert usage.messages_in_context == 2
    assert usage.messages_dropped == 0
    assert usage.memory_tokens > 0
    assert usage.window is None

    tight = measure_context(PATH, context_window=4, reserve_for_output=0)
    assert tight.messages_in_context == 1
    assert tight.messages_dropped == 3
