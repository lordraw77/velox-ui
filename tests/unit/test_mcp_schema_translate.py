"""MCP tool schema <-> ``ToolSpec`` translation."""

from __future__ import annotations

from velox_ui.mcp.client import McpTool, McpToolResult
from velox_ui.mcp.schema_translate import (
    render_tool_result,
    split_qualified_name,
    to_tool_spec,
    to_tool_specs,
)


def test_to_tool_spec_qualifies_the_name_with_the_server() -> None:
    tool = McpTool(
        name="search",
        description="Search the web.",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    spec = to_tool_spec(tool, server_name="brave")
    assert spec.name == "brave__search"
    assert spec.description == "Search the web."
    assert spec.parameters == tool.input_schema


def test_to_tool_spec_falls_back_to_an_empty_object_schema() -> None:
    tool = McpTool(name="ping", input_schema={"type": "string"})
    spec = to_tool_spec(tool, server_name="s")
    assert spec.parameters == {"type": "object", "properties": {}}


def test_to_tool_spec_synthesizes_a_description_when_none_given() -> None:
    tool = McpTool(name="ping")
    spec = to_tool_spec(tool, server_name="s")
    assert "ping" in spec.description
    assert "s" in spec.description


def test_to_tool_specs_translates_every_tool() -> None:
    tools = [McpTool(name="a"), McpTool(name="b")]
    specs = to_tool_specs(tools, server_name="srv")
    assert [s.name for s in specs] == ["srv__a", "srv__b"]


def test_split_qualified_name_round_trips() -> None:
    assert split_qualified_name("srv__tool") == ("srv", "tool")


def test_split_qualified_name_none_for_unqualified() -> None:
    assert split_qualified_name("calculator") is None


def test_render_tool_result_returns_content() -> None:
    result = McpToolResult(content="42")
    assert render_tool_result(result) == "42"


def test_render_tool_result_summarizes_empty_content() -> None:
    result = McpToolResult(content="")
    assert render_tool_result(result) != ""
