"""Modelfile parsing.

The interface lets a person create a model by writing a Modelfile, because that is the
format the Ollama documentation and community use. Ollama's ``/api/create`` no longer
accepts Modelfile text, though: it takes structured fields. This module is the
translation, and it is strict about the one thing it cannot translate — a ``FROM`` that
names a file on disk rather than an installed model, which would need the file uploaded
to the Ollama host first.

Errors name the line, because a Modelfile is typed by hand.
"""

from __future__ import annotations

import re
from typing import Any

from velox_ui.errors import ValidationError
from velox_ui.providers.base import CreateModelSpec

__all__ = ["coerce_parameter", "parse_modelfile"]

_INSTRUCTION = re.compile(r"^\s*([A-Za-z]+)\s+(.*)$", re.DOTALL)
_FILE_REFERENCE = re.compile(r"^(?:[./~]|[A-Za-z]:\\)|\.(?:gguf|bin|safetensors)$|sha256[-:]")
_MESSAGE_ROLES = frozenset({"system", "user", "assistant"})


def coerce_parameter(raw: str) -> Any:
    """Convert one Modelfile parameter value to its natural type.

    Quoted values stay strings; ``true``/``false`` become booleans; anything numeric
    becomes an int or a float.
    """
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def parse_modelfile(text: str, *, name: str) -> CreateModelSpec:
    """Parse Modelfile text into a create request.

    Supported instructions: ``FROM``, ``PARAMETER``, ``SYSTEM``, ``TEMPLATE``,
    ``MESSAGE`` and ``LICENSE``. Values may use triple quotes to span lines.

    Args:
        text: The Modelfile.
        name: Name of the model to create.

    Returns:
        The equivalent structured request.

    Raises:
        ValidationError: On an unknown instruction, a missing or file-based ``FROM``,
            an unterminated triple quote, or a malformed ``PARAMETER``/``MESSAGE``.
    """
    from_model: str | None = None
    system: str | None = None
    template: str | None = None
    license_text: str | None = None
    parameters: dict[str, Any] = {}
    messages: list[dict[str, str]] = []

    for line_number, instruction, value in _instructions(text):
        where = f"Modelfile line {line_number}"
        if instruction == "FROM":
            if _FILE_REFERENCE.search(value):
                raise ValidationError(
                    f"{where}: FROM must name a model installed on the host. Importing "
                    "GGUF or safetensors files is not supported from the interface."
                )
            from_model = value
        elif instruction == "PARAMETER":
            key, _, raw = value.partition(" ")
            if not key or not raw.strip():
                raise ValidationError(f"{where}: PARAMETER needs a name and a value.")
            coerced = coerce_parameter(raw.strip())
            if key in parameters:
                existing = parameters[key]
                parameters[key] = (
                    [*existing, coerced] if isinstance(existing, list) else [existing, coerced]
                )
            else:
                parameters[key] = [coerced] if key == "stop" else coerced
        elif instruction == "SYSTEM":
            system = value
        elif instruction == "TEMPLATE":
            template = value
        elif instruction == "LICENSE":
            license_text = value
        elif instruction == "MESSAGE":
            role, _, content = value.partition(" ")
            if role.lower() not in _MESSAGE_ROLES or not content.strip():
                raise ValidationError(
                    f"{where}: MESSAGE needs a role (system, user or assistant) and text."
                )
            messages.append({"role": role.lower(), "content": _unquote(content.strip())})
        elif instruction == "ADAPTER":
            raise ValidationError(
                f"{where}: ADAPTER references a file on disk, which is not supported "
                "from the interface."
            )
        else:
            raise ValidationError(f"{where}: unknown instruction {instruction!r}.")

    if from_model is None:
        raise ValidationError("The Modelfile needs a FROM line naming an installed model.")
    return CreateModelSpec(
        name=name,
        from_model=from_model,
        system=system,
        template=template,
        parameters=parameters,
        messages=tuple(messages),
        license=license_text,
    )


def _instructions(text: str) -> list[tuple[int, str, str]]:
    """Split a Modelfile into ``(line, INSTRUCTION, value)`` triples."""
    found: list[tuple[int, str, str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        line_number = index + 1
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _INSTRUCTION.match(line)
        if match is None:
            raise ValidationError(f"Modelfile line {line_number}: expected an instruction.")
        instruction, value = match.group(1).upper(), match.group(2).strip()

        if '"""' in value:
            head, _, rest = value.partition('"""')
            if '"""' in rest:
                body, _, _ = rest.partition('"""')
            else:
                collected = [rest]
                while index < len(lines) and '"""' not in lines[index]:
                    collected.append(lines[index])
                    index += 1
                if index >= len(lines):
                    raise ValidationError(
                        f"Modelfile line {line_number}: the triple-quoted value never ends."
                    )
                collected.append(lines[index].partition('"""')[0])
                index += 1
                body = "\n".join(collected)
            value = f"{head}{body}" if head.strip() else body
            # A triple-quoted body is verbatim apart from the newline that follows the
            # opening quotes, which is formatting rather than content.
            value = value.removeprefix("\n")
        else:
            value = _unquote(value)
        found.append((line_number, instruction, value))
    return found


def _unquote(value: str) -> str:
    """Strip one pair of surrounding double quotes."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value
