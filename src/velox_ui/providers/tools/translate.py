"""``ToolSpec``/``ToolCall`` <-> each provider's wire format for tool calling.

Factored out of the adapters so the OpenAI-style shape (used by ``openai_compat`` and
therefore by every OpenAI-protocol preset, Gemini's OpenAI-compatible route included)
and the Anthropic shape each exist exactly once. Gemini does not get its own function
here: it is reached exclusively through ``openai_compat`` (ADR-0009), so it already
gets the OpenAI translation with no separate code path — the "OpenAI <-> Gemini <->
Anthropic" framing in docs/design/01-repo-layout.md collapses to these two functions
in practice.
"""

from __future__ import annotations

from typing import Any

from velox_ui.providers.base import ToolChoice, ToolSpec

__all__ = [
    "anthropic_tool_choice",
    "to_anthropic_tools",
    "to_openai_tools",
]


def to_openai_tools(tools: tuple[ToolSpec, ...] | None) -> list[dict[str, Any]] | None:
    """Render tools in the OpenAI ``tools`` array shape.

    Returns ``None`` for no tools, so callers can do ``if tools:`` on the request
    struct without materializing an empty list into the request body.
    """
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def to_anthropic_tools(tools: tuple[ToolSpec, ...] | None) -> list[dict[str, Any]] | None:
    """Render tools in the Anthropic ``tools`` array shape."""
    if not tools:
        return None
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
        for tool in tools
    ]


def anthropic_tool_choice(choice: ToolChoice | None) -> dict[str, Any] | None:
    """Render a :class:`ToolChoice` in Anthropic's ``tool_choice`` shape."""
    if choice is None:
        return None
    if choice.mode == "required":
        return {"type": "any"}
    if choice.mode == "named" and choice.name:
        return {"type": "tool", "name": choice.name}
    if choice.mode == "none":
        return None
    return {"type": "auto"}
