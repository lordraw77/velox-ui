"""Prompt-based tool calling for models with no native support.

``Capabilities.tools`` reports ``NONE`` for most local chat models: their backend
either does not expose an OpenAI-style ``tools`` request field at all (llama.cpp's
``/completion``, which this module's caller can instead constrain with
:mod:`velox_ui.providers.tools.gbnf`) or the checkpoint itself was never fine-tuned
for structured function calling. There is no way to give such a model a reliable tool
call short of one it was trained to answer with — grammar-constrained decoding gets
the *shape* right (:mod:`gbnf`), never whether the model actually chose to call a
tool sensibly, or filled in sane arguments. This module is therefore explicitly
best-effort: it asks nicely, in the system prompt, and parses whatever comes back. A
model that ignores the instructions or produces malformed JSON simply does not call a
tool that turn — :func:`parse_tool_call` returns ``None`` rather than guessing, and
the turn proceeds as an ordinary text reply.

ADR-0020 records why this — rather than treating emulated tool calling as out of
scope entirely — is the decision for phase 8.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from velox_ui.providers.base import ToolCall, ToolSpec

__all__ = ["TOOL_CALL_CLOSE", "TOOL_CALL_OPEN", "build_tool_prompt", "parse_tool_call"]

TOOL_CALL_OPEN = "<tool_call>"
TOOL_CALL_CLOSE = "</tool_call>"

_CALL_PATTERN = re.compile(
    re.escape(TOOL_CALL_OPEN) + r"\s*(.*?)\s*" + re.escape(TOOL_CALL_CLOSE), re.DOTALL
)


def build_tool_prompt(tools: Sequence[ToolSpec]) -> str:
    """Render a system-prompt addendum describing the available tools.

    Args:
        tools: The tools offered for this turn.

    Returns:
        Instructions plus each tool's name, description and JSON Schema parameters,
        asking the model to answer with a single ``<tool_call>{"name":...,
        "arguments":{...}}</tool_call>`` block when it wants to use one, and ordinary
        text otherwise.
    """
    lines = [
        "You have access to the following tools. To use one, respond with *only* a "
        "single block of the exact form:",
        f'{TOOL_CALL_OPEN}{{"name": "<tool name>", "arguments": {{...}}}}{TOOL_CALL_CLOSE}',
        "Use a tool only when it is needed to answer the request. Otherwise, reply "
        "normally in plain text. Never mix a tool call with other text in the same "
        "response.",
        "",
        "Available tools:",
    ]
    for tool in tools:
        lines.append(f"- {tool.name}: {tool.description}")
        lines.append(f"  parameters (JSON Schema): {json.dumps(tool.parameters)}")
    return "\n".join(lines)


def parse_tool_call(text: str, *, call_id: str) -> ToolCall | None:
    """Best-effort extraction of one tool call from generated text.

    Args:
        text: The model's full text output for the turn.
        call_id: An id to attach to the call, since emulated models invent none.

    Returns:
        A :class:`ToolCall` if a well-formed ``<tool_call>`` block with a ``name`` and
        object ``arguments`` was found, otherwise ``None`` — never raises on
        malformed input, since a model going off-script is an ordinary outcome, not
        an error condition.
    """
    match = _CALL_PATTERN.search(text)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    arguments = payload.get("arguments", {})
    if not isinstance(name, str) or not name or not isinstance(arguments, dict):
        return None
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def strip_tool_call(text: str) -> str:
    """Remove a ``<tool_call>`` block from text, for what is shown as the reply."""
    return _CALL_PATTERN.sub("", text).strip()
