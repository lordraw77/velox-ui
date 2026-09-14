"""JSON Schema -> GBNF grammar generation."""

from __future__ import annotations

import pytest

from velox_ui.providers.base import ToolSpec
from velox_ui.providers.tools.gbnf import tool_call_grammar


def test_tool_call_grammar_requires_at_least_one_tool() -> None:
    with pytest.raises(ValueError, match="at least one tool"):
        tool_call_grammar([])


def test_tool_call_grammar_has_a_root_rule_naming_every_tool() -> None:
    tools = [
        ToolSpec(name="get_weather", description="d", parameters={"type": "object"}),
        ToolSpec(name="search", description="d", parameters={"type": "object"}),
    ]
    grammar = tool_call_grammar(tools)
    assert grammar.startswith("root ::=")
    assert "get_weather" in grammar
    assert "search" in grammar
    assert "args-0 ::=" in grammar
    assert "args-1 ::=" in grammar


def test_tool_call_grammar_constrains_required_string_properties() -> None:
    tools = [
        ToolSpec(
            name="get_weather",
            description="d",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
    ]
    grammar = tool_call_grammar(tools)
    assert "city" in grammar
    assert "string" in grammar


def test_tool_call_grammar_falls_back_for_mixed_required_and_optional() -> None:
    tools = [
        ToolSpec(
            name="t",
            description="d",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                "required": ["a"],
            },
        )
    ]
    grammar = tool_call_grammar(tools)
    assert "args-0 ::= object" in grammar


def test_tool_call_grammar_falls_back_for_unsupported_keywords() -> None:
    tools = [ToolSpec(name="t", description="d", parameters={"oneOf": [{"type": "string"}]})]
    grammar = tool_call_grammar(tools)
    assert "args-0 ::= json-value" in grammar
