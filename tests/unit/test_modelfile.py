"""Modelfile text to a structured create request."""

from __future__ import annotations

import pytest

from velox_ui.errors import ValidationError
from velox_ui.providers.modelfile import parse_modelfile

EXAMPLE = '''
# A terse assistant built on llama3.2
FROM llama3.2:latest
PARAMETER temperature 0.2
PARAMETER num_ctx 8192
PARAMETER stop "<|start_header_id|>"
PARAMETER stop "<|end_header_id|>"
SYSTEM "Answer in one sentence."
TEMPLATE """{{ if .System }}<|system|>
{{ .System }}{{ end }}
<|user|>
{{ .Prompt }}"""
MESSAGE user Is Turin in Italy?
MESSAGE assistant """Yes."""
'''


def test_a_complete_modelfile_translates() -> None:
    spec = parse_modelfile(EXAMPLE, name="terse")
    assert spec.name == "terse"
    assert spec.from_model == "llama3.2:latest"
    assert spec.parameters == {
        "temperature": 0.2,
        "num_ctx": 8192,
        "stop": ["<|start_header_id|>", "<|end_header_id|>"],
    }
    assert spec.system == "Answer in one sentence."
    assert (
        spec.template
        == "{{ if .System }}<|system|>\n{{ .System }}{{ end }}\n<|user|>\n{{ .Prompt }}"
    )
    assert spec.messages == (
        {"role": "user", "content": "Is Turin in Italy?"},
        {"role": "assistant", "content": "Yes."},
    )


def test_a_single_stop_is_still_a_list() -> None:
    # Ollama expects `stop` as an array even when there is one sequence.
    spec = parse_modelfile('FROM qwen3\nPARAMETER stop "###"', name="x")
    assert spec.parameters == {"stop": ["###"]}


def test_instructions_are_case_insensitive() -> None:
    assert parse_modelfile("from qwen3\nsystem hello", name="x").system == "hello"


@pytest.mark.parametrize(
    "source",
    [
        "FROM ./model.gguf",
        "FROM /usr/share/ollama/.ollama/models/blobs/sha256-3d0b790534fe",
        "FROM ~/weights/model.safetensors",
    ],
)
def test_file_based_from_is_refused_with_a_reason(source: str) -> None:
    with pytest.raises(ValidationError, match="installed"):
        parse_modelfile(source, name="x")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("SYSTEM hi", "FROM"),
        ("FROM qwen3\nBOGUS value", "line 2"),
        ('FROM qwen3\nTEMPLATE """never closed', "never ends"),
        ("FROM qwen3\nADAPTER ./lora.gguf", "ADAPTER"),
        ("FROM qwen3\nPARAMETER temperature", "PARAMETER"),
        ("FROM qwen3\nMESSAGE robot hello", "MESSAGE"),
    ],
)
def test_errors_name_the_problem(text: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_modelfile(text, name="x")


def test_show_parameters_keep_a_single_stop_as_a_list() -> None:
    # Found against a real Ollama 0.34 host: a model created with one stop sequence is
    # reported with a single `stop` line, which must not turn into a bare string.
    from velox_ui.providers.ollama import parse_parameters

    text = 'stop                           "###"\ntemperature                    0.3'
    assert parse_parameters(text) == {"stop": ["###"], "temperature": 0.3}
