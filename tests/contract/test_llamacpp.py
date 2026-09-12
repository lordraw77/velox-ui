"""llama.cpp adapter against a fake replaying ``llama-server``'s real wire format."""

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
    TextDelta,
    Usage,
)
from velox_ui.providers.errors import BackendOffline, ContextOverflow
from velox_ui.providers.llamacpp import LlamaCppProvider

pytestmark = pytest.mark.contract


def _provider(server: FakeServer, client: httpx.AsyncClient) -> LlamaCppProvider:
    return LlamaCppProvider("llamacpp-test", server.base_url, client)


def _request(prompt: str = "hello") -> ChatRequest:
    return ChatRequest(
        model="qwen2.5-7b-instruct",
        messages=(ChatMessage(role="user", content=prompt),),
    )


async def test_context_window_comes_from_props(llamacpp_server, http_client) -> None:
    capabilities = await _provider(llamacpp_server, http_client).capabilities("any")
    assert capabilities.context_window == 8192
    assert capabilities.grammar is True, "GBNF is the reason this adapter exists"
    assert capabilities.chat_template


async def test_streams_text_then_usage_then_done(llamacpp_server, http_client) -> None:
    events = [
        event async for event in _provider(llamacpp_server, http_client).stream_chat(_request())
    ]

    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text == "The quick brown fox."

    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.tokens_in == 18
    assert usage.tokens_out == 5
    # Taken from the backend's own `timings`, not recomputed here.
    assert usage.tokens_per_second == pytest.approx(23.7)
    assert usage.prompt_eval_ms == pytest.approx(120.5)
    assert usage.eval_ms == pytest.approx(210.75)

    assert isinstance(events[-1], Done)
    assert events[-1].finish_reason == "stop"


async def test_prompt_cache_is_on_by_default(llamacpp_server, http_client) -> None:
    # Without cache_prompt, every turn of a long conversation re-processes the whole
    # context. It has to default to on, not be something the caller remembers.
    body = _provider(llamacpp_server, http_client)._encode_request(_request())
    assert body["cache_prompt"] is True


async def test_prompt_cache_can_be_turned_off(llamacpp_server, http_client) -> None:
    request = ChatRequest(
        model="m",
        messages=(ChatMessage(role="user", content="x"),),
        params=SamplingParams(cache_prompt=False),
    )
    body = _provider(llamacpp_server, http_client)._encode_request(request)
    assert body["cache_prompt"] is False


async def test_grammar_is_passed_through(llamacpp_server, http_client) -> None:
    grammar = 'root ::= "yes" | "no"'
    request = ChatRequest(
        model="m", messages=(ChatMessage(role="user", content="x"),), grammar=grammar
    )
    body = _provider(llamacpp_server, http_client)._encode_request(request)
    assert body["grammar"] == grammar


async def test_sampling_parameters_reach_the_backend(llamacpp_server, http_client) -> None:
    request = ChatRequest(
        model="m",
        messages=(ChatMessage(role="user", content="x"),),
        params=SamplingParams(min_p=0.05, typical_p=0.9, mirostat=2, max_tokens=256, seed=7),
    )
    body = _provider(llamacpp_server, http_client)._encode_request(request)
    assert body["min_p"] == 0.05
    assert body["typical_p"] == 0.9
    assert body["mirostat"] == 2
    assert body["n_predict"] == 256
    assert body["seed"] == 7
    assert "top_k" not in body, "unset parameters must not be sent as null"


async def test_context_overflow_is_typed(llamacpp_server, http_client) -> None:
    # llama-server does give a structured type here, unlike Ollama. The adapter must
    # use it rather than fall back to substring matching.
    with pytest.raises(ContextOverflow):
        async for _ in _provider(llamacpp_server, http_client).stream_chat(
            _request("__context__")
        ):
            pass


async def test_first_token_arrives_before_the_stream_ends(llamacpp_server, http_client) -> None:
    provider = _provider(llamacpp_server, http_client)
    started = time.perf_counter()
    first_delta_at: float | None = None
    async for event in provider.stream_chat(_request("__slow__")):
        if isinstance(event, TextDelta) and first_delta_at is None:
            first_delta_at = time.perf_counter() - started
            break
    assert first_delta_at is not None
    assert first_delta_at < 2.0, "the adapter buffered instead of streaming"


async def test_health_reports_up(llamacpp_server, http_client) -> None:
    health = await _provider(llamacpp_server, http_client).health()
    assert health.state is HealthState.UP


async def test_health_of_an_unreachable_host_never_raises(http_client) -> None:
    provider = LlamaCppProvider("llamacpp-down", "http://127.0.0.1:1", http_client)
    assert (await provider.health()).state is HealthState.DOWN


async def test_unreachable_host_is_offline(http_client) -> None:
    provider = LlamaCppProvider("llamacpp-down", "http://127.0.0.1:1", http_client)
    with pytest.raises(BackendOffline):
        async for _ in provider.stream_chat(_request()):
            pass


async def test_models_prefers_the_models_endpoint(llamacpp_server, http_client) -> None:
    models = await _provider(llamacpp_server, http_client).list_models()
    assert [model.key for model in models] == ["qwen2.5-7b-instruct"]
    assert models[0].capabilities.context_window == 8192


async def test_embeddings(llamacpp_server, http_client) -> None:
    vectors = await _provider(llamacpp_server, http_client).embed("m", ["a", "b"])
    assert len(vectors) == 2
