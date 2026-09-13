"""Model management against fakes of Ollama and llama-server.

The Ollama responses these replay were captured from a real 0.34 host, including the
two details that broke naive parsing: a pull of a nonexistent model answers HTTP 200
and reports the failure *inside* the stream, and ``/api/show`` returns parameters as
padded Modelfile text rather than JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.ollama import REQUESTS
from velox_ui.providers.base import CreateModelSpec, LocalModelAdmin, ModelInspector
from velox_ui.providers.errors import ModelNotFound
from velox_ui.providers.llamacpp import LlamaCppProvider
from velox_ui.providers.ollama import OllamaProvider
from velox_ui.providers.registry import features

pytestmark = pytest.mark.contract


def _ollama(server, client) -> OllamaProvider:
    return OllamaProvider("ollama-test", server.base_url, client)


def _last(path: str) -> dict:
    return next(body for seen, body in reversed(REQUESTS) if seen == path)


def test_capabilities_are_declared_by_protocol_not_by_name(
    ollama_server, llamacpp_server
) -> None:
    ollama = OllamaProvider("o", ollama_server.base_url, None)  # type: ignore[arg-type]
    llama = LlamaCppProvider("l", llamacpp_server.base_url, None)  # type: ignore[arg-type]
    assert isinstance(ollama, LocalModelAdmin) and isinstance(ollama, ModelInspector)
    # llama-server reports what it has loaded but cannot download or delete anything.
    assert isinstance(llama, ModelInspector) and not isinstance(llama, LocalModelAdmin)
    assert "pull" in features(ollama) and "pull" not in features(llama)


async def test_pull_reports_progress_per_layer(ollama_server, http_client) -> None:
    progress = [step async for step in _ollama(ollama_server, http_client).pull("llama3.2")]
    assert progress[0].status == "pulling manifest"
    assert {step.digest for step in progress if step.digest} == {"sha256:aaaa", "sha256:bbbb"}
    assert progress[-1].status == "success"
    assert _last("/api/pull") == {"model": "llama3.2", "stream": True}


async def test_a_failed_pull_inside_a_200_stream_is_an_error(
    ollama_server, http_client
) -> None:
    with pytest.raises(ModelNotFound):
        async for _ in _ollama(ollama_server, http_client).pull("ghost"):
            pass


async def test_create_sends_structured_fields(ollama_server, http_client) -> None:
    spec = CreateModelSpec(
        name="terse",
        from_model="llama3.2",
        system="Answer in one sentence.",
        parameters={"temperature": 0.2, "stop": ["<|end|>"]},
    )
    steps = [step async for step in _ollama(ollama_server, http_client).create(spec)]
    assert steps[-1].status == "success"
    assert _last("/api/create") == {
        "model": "terse",
        "from": "llama3.2",
        "system": "Answer in one sentence.",
        "parameters": {"temperature": 0.2, "stop": ["<|end|>"]},
        "stream": True,
    }


async def test_create_from_a_missing_model_fails(ollama_server, http_client) -> None:
    with pytest.raises(ModelNotFound):
        async for _ in _ollama(ollama_server, http_client).create(
            CreateModelSpec(name="x", from_model="ghost")
        ):
            pass


@pytest.mark.parametrize("operation", ["delete", "copy", "show"])
async def test_missing_models_are_typed(ollama_server, http_client, operation) -> None:
    provider = _ollama(ollama_server, http_client)
    with pytest.raises(ModelNotFound):
        if operation == "delete":
            await provider.delete("ghost")
        elif operation == "copy":
            await provider.copy("ghost", "ghost-copy")
        else:
            await provider.show("ghost")


async def test_show_parses_modelfile_parameters(ollama_server, http_client) -> None:
    details = await _ollama(ollama_server, http_client).show("llama3.2")
    assert details.parameters == {
        "stop": ["<|im_start|>", "<|im_end|>"],
        "temperature": 0.6,
        "top_k": 20,
    }
    assert details.context_length == 131072
    assert details.parameter_size == "3.2B"
    assert details.capabilities == ("completion", "tools")
    assert details.has_license
    expected = datetime(2026, 9, 8, 6, 41, 58, 117042, tzinfo=UTC)
    assert details.modified_at_ms == int(expected.timestamp() * 1000)


async def test_running_reads_nanosecond_timestamps(ollama_server, http_client) -> None:
    running = await _ollama(ollama_server, http_client).running()
    entry = next(model for model in running if model.name == "llama3.2")
    expected = datetime(2026, 9, 13, 9, 1, 46, 726485, tzinfo=UTC)
    assert entry.expires_at_ms == int(expected.timestamp() * 1000)
    assert entry.context_length == 4096
    assert entry.vram_bytes == 2_019_393_189


async def test_unload_uses_keep_alive_zero(ollama_server, http_client) -> None:
    await _ollama(ollama_server, http_client).unload("llama3.2")
    assert _last("/api/generate") == {"model": "llama3.2", "keep_alive": 0}


async def test_copy_and_delete_send_the_documented_bodies(ollama_server, http_client) -> None:
    provider = _ollama(ollama_server, http_client)
    await provider.copy("llama3.2", "llama3.2-backup")
    assert _last("/api/copy") == {"source": "llama3.2", "destination": "llama3.2-backup"}
    await provider.delete("llama3.2-backup")
    assert _last("/api/delete") == {"model": "llama3.2-backup"}


async def test_llamacpp_running_comes_from_slots(llamacpp_server, http_client) -> None:
    provider = LlamaCppProvider("llamacpp-test", llamacpp_server.base_url, http_client)
    running = await provider.running()
    assert len(running) == 1
    assert running[0].name == "qwen2.5-7b-instruct"
    assert running[0].busy is True, "one of the two slots is processing"
    assert running[0].context_length == 8192

    details = await provider.show("qwen2.5-7b-instruct")
    assert details.context_length == 8192
    assert details.template is not None
