"""The parametrized OpenAI-compatible adapter against a fake replaying the real format.

The fake sends what servers actually send — a role-only first chunk, per-token frames,
a usage-only chunk with an empty ``choices`` array, and a literal ``[DONE]`` — so these
tests fail if the adapter's parsing depends on a tidier stream than reality provides.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import httpx
import pytest

from tests.fakes.openai_compat import create_app as create_openai
from tests.fakes.server import FakeServer, run_fake
from velox_ui.providers.base import (
    ChatMessage,
    ChatRequest,
    Done,
    HealthState,
    ReasoningDelta,
    SamplingParams,
    Status,
    StatusPhase,
    TextDelta,
    ToolCallDelta,
    Usage,
)
from velox_ui.providers.errors import (
    AuthError,
    BackendOffline,
    ContextOverflow,
    ModelLoading,
    ModelNotFound,
    RateLimited,
    UpstreamError,
)
from velox_ui.providers.openai_compat import OpenAICompatProvider
from velox_ui.providers.presets import get_preset

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def recording() -> Iterator[tuple[FakeServer, list[dict]]]:
    """A fake whose received request bodies the test can inspect."""
    app = create_openai()
    with run_fake(app) as server:
        yield server, app.state.requests


def _provider(
    server: FakeServer,
    client: httpx.AsyncClient,
    preset: str = "vllm",
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> OpenAICompatProvider:
    found = get_preset(preset)
    assert found is not None
    return OpenAICompatProvider(
        "compat-test",
        base_url or f"{server.base_url}/v1",
        client,
        preset=found,
        api_key=api_key,
    )


def _request(model: str = "qwen2.5-7b-instruct", **params: object) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=(ChatMessage(role="user", content="hello"),),
        params=SamplingParams(**params),  # type: ignore[arg-type]
    )


async def _events(provider: OpenAICompatProvider, request: ChatRequest) -> list[object]:
    return [event async for event in provider.stream_chat(request)]


async def test_streams_text_then_usage_then_done(recording, http_client) -> None:
    server, _ = recording
    events = await _events(_provider(server, http_client), _request())

    assert isinstance(events[0], Status) and events[0].phase is StatusPhase.GENERATING
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text == "Local models are first."

    usage = next(event for event in events if isinstance(event, Usage))
    assert (usage.tokens_in, usage.tokens_out) == (12, 5)
    # No timings object on this stream, so the rate is measured from chunk arrival.
    assert usage.tokens_per_second is not None and usage.tokens_per_second > 0
    assert isinstance(events[-1], Done) and events[-1].finish_reason == "stop"


async def test_parameters_are_filtered_and_renamed_per_preset(recording, http_client) -> None:
    server, requests = recording
    await _events(
        _provider(server, http_client, "vllm"),
        _request(
            temperature=0.5,
            top_k=40,
            repeat_penalty=1.1,
            mirostat=2,
            num_ctx=4096,
            max_tokens=64,
            stop=("END",),
        ),
    )
    body = requests[-1]
    assert body["temperature"] == 0.5
    assert body["top_k"] == 40
    assert body["repetition_penalty"] == 1.1, "vLLM spells it repetition_penalty"
    assert "repeat_penalty" not in body
    assert "mirostat" not in body, "vLLM does not take mirostat; it must not be sent"
    assert "num_ctx" not in body, "num_ctx is an Ollama option, not an OpenAI field"
    assert body["max_tokens"] == 64
    assert body["stop"] == ["END"]
    assert body["stream_options"] == {"include_usage": True}


async def test_tabbyapi_names_its_samplers_differently(recording, http_client) -> None:
    server, requests = recording
    await _events(
        _provider(server, http_client, "tabbyapi", api_key="unused"),
        _request(mirostat=2, typical_p=0.9, tfs_z=0.95),
    )
    body = requests[-1]
    assert body["mirostat_mode"] == 2
    assert body["typical"] == 0.9
    assert body["tfs"] == 0.95


@pytest.mark.parametrize("model", ["reasoner", "thinker"])
async def test_reasoning_is_read_from_either_field(recording, http_client, model) -> None:
    server, _ = recording
    events = await _events(_provider(server, http_client), _request(model))
    reasoning = "".join(event.text for event in events if isinstance(event, ReasoningDelta))
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert reasoning == "The user said hi."
    assert text == "Local models are first."


async def test_backend_timings_are_used_verbatim(recording, http_client) -> None:
    server, _ = recording
    events = await _events(_provider(server, http_client, "llamafile"), _request("timed"))
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.tokens_per_second == 50.0
    assert usage.prompt_eval_ms == 48.0
    assert usage.eval_ms == 100.0
    assert usage.tokens_in == 12


async def test_tool_call_fragments_keep_their_id_and_name(recording, http_client) -> None:
    server, _ = recording
    events = await _events(_provider(server, http_client), _request("tools"))
    calls = [event for event in events if isinstance(event, ToolCallDelta)]
    assert {call.id for call in calls} == {"call_abc"}
    assert {call.name for call in calls} == {"get_weather"}
    assert "".join(call.arguments_fragment for call in calls) == '{"city": "Turin"}'
    assert isinstance(events[-1], Done) and events[-1].finish_reason == "tool_calls"


@pytest.mark.parametrize(
    ("model", "error"),
    [
        ("missing", ModelNotFound),
        ("ctx", ContextOverflow),
        ("limited", RateLimited),
        ("loading", ModelLoading),
    ],
)
async def test_errors_are_typed(recording, http_client, model, error) -> None:
    server, _ = recording
    with pytest.raises(error) as caught:
        await _events(_provider(server, http_client), _request(model))
    if error is RateLimited:
        assert caught.value.retry_after_s == 7.0
        assert caught.value.retryable
    if error is ModelLoading:
        assert not caught.value.retryable, "retrying a loading model loads it twice"


async def test_credentials_are_sent_and_their_rejection_is_typed(
    openai_keyed_server, http_client
) -> None:
    good = _provider(openai_keyed_server, http_client, api_key="sk-test-key-1234")
    assert (await good.list_models())[0].key == "qwen2.5-7b-instruct"
    assert (await good.health()).state is HealthState.UP

    bad = _provider(openai_keyed_server, http_client, api_key="sk-wrong")
    with pytest.raises(AuthError):
        await _events(bad, _request())
    health = await bad.health()
    assert health.state is HealthState.DEGRADED, "a rejected key is not an offline host"
    assert "sk-wrong" not in repr(bad), "the credential must not appear in a repr"


async def test_context_window_comes_from_the_listing(recording, http_client) -> None:
    server, _ = recording
    provider = _provider(server, http_client)
    models = {model.key: model for model in await provider.list_models()}
    assert models["qwen2.5-7b-instruct"].capabilities.context_window == 32768
    assert models["text-embedding"].capabilities.context_window is None, "never guessed"


async def test_embeddings_are_paired_by_index(recording, http_client) -> None:
    server, _ = recording
    vectors = await _provider(server, http_client).embed("text-embedding", ["a", "b"])
    assert vectors == [[0.0, 0.5], [1.0, 0.5]]


async def test_a_missing_api_prefix_is_explained(recording, http_client) -> None:
    server, _ = recording
    provider = _provider(server, http_client, "custom", base_url=server.base_url)
    with pytest.raises(UpstreamError) as caught:
        await provider.list_models()
    assert "/v1" in caught.value.message


async def test_an_unreachable_backend_is_offline(http_client) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    preset = get_preset("lmstudio")
    assert preset is not None
    provider = OpenAICompatProvider(
        "gone", f"http://127.0.0.1:{port}/v1", http_client, preset=preset
    )
    with pytest.raises(BackendOffline):
        await _events(provider, _request())
    assert (await provider.health()).state is HealthState.DOWN
