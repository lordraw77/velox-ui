"""Prompt-based emulated tool calling: prompt construction and the best-effort parser."""

from __future__ import annotations

from velox_ui.providers.base import ToolSpec
from velox_ui.providers.tools.emulated import (
    TOOL_CALL_CLOSE,
    TOOL_CALL_OPEN,
    build_tool_prompt,
    parse_tool_call,
    strip_tool_call,
)

_TOOLS = (
    ToolSpec(
        name="get_weather",
        description="Look up the weather.",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
    ),
)


def test_build_tool_prompt_lists_every_tool() -> None:
    prompt = build_tool_prompt(_TOOLS)
    assert "get_weather" in prompt
    assert "Look up the weather." in prompt
    assert TOOL_CALL_OPEN in prompt


def test_parse_tool_call_extracts_a_well_formed_call() -> None:
    text = (
        f"Sure, let me check.{TOOL_CALL_OPEN}"
        f'{{"name": "get_weather", "arguments": {{"city": "Turin"}}}}'
        f"{TOOL_CALL_CLOSE}"
    )
    call = parse_tool_call(text, call_id="call-1")
    assert call is not None
    assert call.id == "call-1"
    assert call.name == "get_weather"
    assert '"city": "Turin"' in call.arguments or '"city":"Turin"' in call.arguments


def test_parse_tool_call_returns_none_for_plain_text() -> None:
    assert parse_tool_call("Just an ordinary answer.", call_id="x") is None


def test_parse_tool_call_returns_none_for_malformed_json() -> None:
    text = f"{TOOL_CALL_OPEN}not json{TOOL_CALL_CLOSE}"
    assert parse_tool_call(text, call_id="x") is None


def test_parse_tool_call_returns_none_without_a_name() -> None:
    text = f'{TOOL_CALL_OPEN}{{"arguments": {{}}}}{TOOL_CALL_CLOSE}'
    assert parse_tool_call(text, call_id="x") is None


def test_strip_tool_call_removes_the_block() -> None:
    text = f'before {TOOL_CALL_OPEN}{{"name": "x", "arguments": {{}}}}{TOOL_CALL_CLOSE} '
    assert strip_tool_call(text) == "before"
