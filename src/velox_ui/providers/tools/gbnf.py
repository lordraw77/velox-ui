"""JSON Schema -> GBNF, for llama.cpp's grammar-constrained decoding.

Scope, deliberately: this covers the subset of JSON Schema that MCP tool parameters
and hand-written ``ToolSpec.parameters`` actually use in practice — ``object``,
``string`` (optionally with ``enum``), ``integer``, ``number``, ``boolean``, ``array``,
and ``null`` — plus one simplification: an object with a mix of required and optional
properties is not enumerated combinatorially (which would make the grammar's size
exponential in the optional-property count); it falls back to a permissive JSON
object for that node instead. A schema using ``oneOf``/``allOf``/``$ref``/``pattern``/
numeric bounds falls back to the permissive ``json-value`` rule (any well-formed JSON
value) the same way, rather than raising — an unusual tool still gets *some*
constraint (valid JSON syntax) even where its exact shape cannot be expressed here.

Used only by the emulated tool-calling path on llama.cpp
(:mod:`velox_ui.providers.tools.emulated`), which has ``ChatRequest.grammar`` to fill;
adapters with native tool support never touch this module.
"""

from __future__ import annotations

import json
from typing import Any

from velox_ui.providers.base import ToolSpec

__all__ = ["tool_call_grammar"]

_COMMON_RULES = (
    'json-value ::= object | array | string | number | ("true" | "false" | "null") ws\n'
    'object ::= "{" ws (member ("," ws member)*)? "}" ws\n'
    'member ::= string ":" ws json-value\n'
    'array ::= "[" ws (json-value ("," ws json-value)*)? "]" ws\n'
    'string ::= "\\"" ([^"\\\\] | "\\\\" .)* "\\"" ws\n'
    'number ::= "-"? [0-9]+ ("." [0-9]+)? ws\n'
    "ws ::= [ \\t\\n]*\n"
)


def tool_call_grammar(tools: list[ToolSpec]) -> str:
    """Build a GBNF grammar constraining output to one tool-call JSON object.

    The root rule is ``{"name": "<one of the tool names>", "arguments": <schema for
    that tool>}``; each tool's arguments schema is compiled independently and gated by
    that tool's literal name, so a model cannot mix one tool's name with another's
    argument shape.

    Args:
        tools: The tools offered this turn. Must be non-empty.

    Returns:
        A complete GBNF grammar, rooted at ``root``.

    Raises:
        ValueError: If ``tools`` is empty.
    """
    if not tools:
        raise ValueError("tool_call_grammar requires at least one tool")

    alternatives: list[str] = []
    rules: list[str] = []
    for index, tool in enumerate(tools):
        args_rule = f"args-{index}"
        rules.append(f"{args_rule} ::= {_compile_schema(tool.parameters, prefix=args_rule)}")
        name_field = json.dumps(json.dumps(tool.name))
        head = '"{\\"name\\": "'
        mid = '", \\"arguments\\": "'
        tail = '" }"'
        alternatives.append(f"( {head} {name_field} {mid} {args_rule} {tail} )")

    root = "root ::= " + " | ".join(alternatives) + "\n"
    return root + "\n".join(rules) + "\n" + _COMMON_RULES


def _compile_schema(schema: dict[str, Any], *, prefix: str) -> str:
    """Compile one JSON Schema node into an inline GBNF rule body."""
    schema_type = schema.get("type")

    if schema_type == "object" or (schema_type is None and "properties" in schema):
        return _compile_object(schema, prefix=prefix)
    if schema_type == "string":
        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            return "( " + " | ".join(json.dumps(json.dumps(str(v))) for v in enum) + " )"
        return "string"
    if schema_type in ("integer", "number"):
        return "number"
    if schema_type == "boolean":
        return '( "true" | "false" )'
    if schema_type == "array":
        items = schema.get("items")
        item_rule = (
            _compile_schema(items, prefix=f"{prefix}-item")
            if isinstance(items, dict)
            else "json-value"
        )
        return f'( "[" ws ({item_rule} ("," ws {item_rule})*)? "]" ws )'
    if schema_type == "null":
        return '"null"'
    return "json-value"


def _compile_object(schema: dict[str, Any], *, prefix: str) -> str:
    """Compile an object schema, falling back to a permissive object for mixed shapes."""
    del prefix
    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    if not properties:
        return '"{}"'
    if required != set(properties):
        return "object"

    parts: list[str] = []
    for index, (name, prop_schema) in enumerate(properties.items()):
        value_rule = _compile_schema(prop_schema, prefix=f"prop-{name}")
        separator = "," if index > 0 else ""
        key_text = f'{separator}"{name}": '
        prefix_literal = json.dumps(key_text)
        parts.append(f"{prefix_literal} {value_rule}")

    body = " ".join(parts)
    return f'( "{{" {body} "}}" )'
