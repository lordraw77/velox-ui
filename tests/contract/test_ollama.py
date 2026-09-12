"""Ollama adapter against a fake replaying Ollama's real wire format.

The point of these is not that the adapter returns *something*, but that it reads the
same bytes a real Ollama produces: NDJSON, metrics in nanoseconds on the final line,
error text without structured codes.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tests.fakes.server import FakeServer
from velox_ui.providers.base import (
    ChatMessage,
    ChatRequest,
    Done,
    HealthState,
    SamplingParams,
    Status,
    StatusPhase,
    TextDelta,
    ToolSupport,
    Usage,
)
from velox_ui.providers.errors import BackendOffline, ModelNotFound, OutOfMemory
from velox_ui.providers.ollama import OllamaProvider

pytestmark = pytest.mark.contract


def _provider(server: FakeServer, client: httpx.AsyncClient) -> OllamaProvider:
    return OllamaProvider("ollama-test", server.base_url, client)


def _request(model: str = "llama3.2") -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=(ChatMessage(role="user", content="hello"),),
        params=SamplingParams(temperature=0.7, num_ctx=4096),
    )


async def test_lists_models_from_tags(ollama_server, http_client) -> None:
    models = await _provider(ollama_server, http_client).list_models()
    assert [model.key for model in models] == ["llama3.2", "nomic-embed-text"]
    assert models[0].capabilities.quantization == "Q4_K_M"
    assert models[0].family == "llama"


async def test_capabilities_come_from_the_backend(ollama_server, http_client) -> None:
    # The context window must be read from /api/show, never inferred from the name:
    # two copies of "llama3.2" can be built with different num_ctx.
    capabilities = await _provider(ollama_server, http_client).capabilities("llama3.2")
    assert capabilities.context_window == 131072
    assert capabilities.tools is ToolSupport.NATIVE
    assert capabilities.quantization == "Q4_K_M"


async def test_streams_text_then_usage_then_done(ollama_server, http_client) -> None:
    events = [
        event async for event in _provider(ollama_server, http_client).stream_chat(_request())
    ]

    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text == "Hello, world! This is velox-ui."

    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.tokens_in == 26
    assert usage.tokens_out == 9
    # 9 tokens in 900 ms, reported by the backend in nanoseconds.
    assert usage.tokens_per_second == pytest.approx(10.0)
    assert usage.prompt_eval_ms == pytest.approx(200.0)
    assert usage.eval_ms == pytest.approx(900.0)
    assert usage.cost_micros == 0, "a local model costs nothing and must say so"

    assert isinstance(events[-1], Done)
    assert events[-1].finish_reason == "stop"


async def test_loading_is_announced_for_a_model_that_is_not_resident(
    ollama_server, http_client
) -> None:
    # Ollama emits no "loading" event; it simply delays the first chunk. The adapter
    # checks /api/ps and says so itself, or the UI would show a silent stall (ADR-0008).
    events = [
        event
        async for event in _provider(ollama_server, http_client).stream_chat(
            _request("unloaded")
        )
    ]
    phases = [event.phase for event in events if isinstance(event, Status)]
    assert phases[0] is StatusPhase.LOADING_MODEL
    assert StatusPhase.GENERATING in phases


async def test_resident_model_does_not_announce_loading(ollama_server, http_client) -> None:
    events = [
        event async for event in _provider(ollama_server, http_client).stream_chat(_request())
    ]
    phases = [event.phase for event in events if isinstance(event, Status)]
    assert StatusPhase.LOADING_MODEL not in phases


async def test_first_token_arrives_before_the_stream_ends(ollama_server, http_client) -> None:
    # The adapter must not accumulate the response. Against a one-token-per-second
    # backend, the first delta has to arrive long before the last.
    provider = _provider(ollama_server, http_client)
    started = time.perf_counter()
    first_delta_at: float | None = None
    async for event in provider.stream_chat(_request("slow")):
        if isinstance(event, TextDelta) and first_delta_at is None:
            first_delta_at = time.perf_counter() - started
            break
    assert first_delta_at is not None
    assert first_delta_at < 2.0, "the adapter buffered instead of streaming"


async def test_out_of_memory_is_typed(ollama_server, http_client) -> None:
    # Ollama has no structured error codes, only prose. The adapter must still
    # produce something the UI can act on.
    with pytest.raises(OutOfMemory):
        async for _ in _provider(ollama_server, http_client).stream_chat(_request("oom")):
            pass


async def test_missing_model_is_typed(ollama_server, http_client) -> None:
    with pytest.raises(ModelNotFound):
        async for _ in _provider(ollama_server, http_client).stream_chat(_request("missing")):
            pass


async def test_unreachable_host_is_offline_not_a_crash(http_client) -> None:
    provider = OllamaProvider("ollama-down", "http://127.0.0.1:1", http_client)
    with pytest.raises(BackendOffline):
        async for _ in provider.stream_chat(_request()):
            pass


async def test_health_of_an_unreachable_host_never_raises(http_client) -> None:
    # A switched-off local host is an ordinary state, reported as a badge (ADR-0008).
    provider = OllamaProvider("ollama-down", "http://127.0.0.1:1", http_client)
    health = await provider.health()
    assert health.state is HealthState.DOWN
    assert health.detail


async def test_health_of_a_live_host(ollama_server, http_client) -> None:
    health = await _provider(ollama_server, http_client).health()
    assert health.state is HealthState.UP
    assert health.latency_ms is not None


async def test_running_models_and_embeddings(ollama_server, http_client) -> None:
    provider = _provider(ollama_server, http_client)
    running = await provider.running()
    assert "llama3.2" in [model.name for model in running]
    assert running[0].vram_bytes

    vectors = await provider.embed("nomic-embed-text", ["a", "b"])
    assert len(vectors) == 2


async def test_pull_reports_progress(ollama_server, http_client) -> None:
    updates = [
        update async for update in _provider(ollama_server, http_client).pull("llama3.2")
    ]
    assert updates[0].status == "pulling manifest"
    assert updates[-1].status == "success"
    assert any(update.completed_bytes == 100 for update in updates)


async def test_sampling_parameters_reach_the_backend(ollama_server, http_client) -> None:
    # The advanced panel is worthless if the adapter drops what it sends.
    provider = _provider(ollama_server, http_client)
    body = provider._encode_request(
        ChatRequest(
            model="llama3.2",
            messages=(ChatMessage(role="user", content="x"),),
            params=SamplingParams(
                temperature=0.1,
                num_ctx=8192,
                num_gpu=99,
                mirostat=2,
                repeat_penalty=1.15,
                stop=("</s>",),
                keep_alive="10m",
            ),
        )
    )
    options = body["options"]
    assert options["num_ctx"] == 8192
    assert options["num_gpu"] == 99
    assert options["mirostat"] == 2
    assert options["repeat_penalty"] == 1.15
    assert options["stop"] == ["</s>"]
    assert body["keep_alive"] == "10m"
    assert "top_p" not in options, "unset parameters must not be sent as null"


async def test_thinking_models_emit_reasoning_separately(ollama_server, http_client) -> None:
    """Reasoning models put their output in ``message.thinking``, not ``content``.

    This was found against a real Ollama host, not against the fake: qwen3 streamed
    dozens of chunks whose ``content`` was empty, and the adapter reported no output
    at all. The fake now reproduces the real shape so the case stays covered.
    """
    from velox_ui.providers.base import ReasoningDelta

    provider = _provider(ollama_server, http_client)
    events = [event async for event in provider.stream_chat(_request("thinking"))]

    reasoning = "".join(event.text for event in events if isinstance(event, ReasoningDelta))
    text = "".join(event.text for event in events if isinstance(event, TextDelta))

    assert reasoning == "Okay, the user wants a greeting."
    assert text == "Hello, world! This is velox-ui."
    assert reasoning not in text, "thinking must not leak into the visible answer"
