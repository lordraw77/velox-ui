"""The Anthropic adapter against a fake replaying the real Messages API envelope."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from tests.fakes.anthropic import create_app as create_anthropic
from tests.fakes.server import FakeServer, run_fake
from velox_ui.providers.anthropic import AnthropicProvider
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
    ToolCall,
    ToolCallDelta,
    Usage,
)
from velox_ui.providers.errors import (
    AuthError,
    ContextOverflow,
    ModelNotFound,
    RateLimited,
)

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def recording() -> Iterator[tuple[FakeServer, list[dict]]]:
    """A fake whose received request bodies the test can inspect."""
    app = create_anthropic()
    with run_fake(app) as server:
        yield server, app.state.requests


def _provider(
    server: FakeServer, client: httpx.AsyncClient, *, api_key: str | None = None
) -> AnthropicProvider:
    return AnthropicProvider("anthropic-test", f"{server.base_url}/v1", client, api_key=api_key)


async def test_list_models(recording, http_client) -> None:
    server, _ = recording
    provider = _provider(server, http_client)
    models = await provider.list_models()
    assert {model.key for model in models} == {"claude-sonnet-5", "claude-haiku-4-5"}
    assert all(model.capabilities.tools == "native" for model in models)


async def test_a_normal_turn_streams_text_then_usage_then_done(recording, http_client) -> None:
    server, requests = recording
    provider = _provider(server, http_client)
    events = [
        event
        async for event in provider.stream_chat(
            ChatRequest(
                model="claude-sonnet-5",
                messages=(ChatMessage(role="user", content="hi"),),
            )
        )
    ]
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text == "Local models are first."
    assert isinstance(events[0], Status) and events[0].phase == StatusPhase.GENERATING
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.tokens_in == 12
    assert usage.tokens_out == 5
    done = events[-1]
    assert isinstance(done, Done) and done.finish_reason == "stop"

    # max_tokens is required by the real API even when nothing was saved.
    assert requests[-1]["max_tokens"] > 0


async def test_the_system_prompt_is_pulled_out_of_the_message_list(
    recording, http_client
) -> None:
    server, requests = recording
    provider = _provider(server, http_client)
    async for _ in provider.stream_chat(
        ChatRequest(
            model="claude-sonnet-5",
            messages=(
                ChatMessage(role="system", content="Be terse."),
                ChatMessage(role="user", content="hi"),
            ),
        )
    ):
        pass
    sent = requests[-1]
    assert sent["system"] == "Be terse."
    assert all(message["role"] != "system" for message in sent["messages"])


async def test_thinking_is_relayed_when_the_model_streams_it_unprompted(
    recording, http_client
) -> None:
    server, _ = recording
    provider = _provider(server, http_client)
    events = [
        event
        async for event in provider.stream_chat(
            ChatRequest(
                model="thinker",
                messages=(ChatMessage(role="user", content="hi"),),
            )
        )
    ]
    reasoning = "".join(event.text for event in events if isinstance(event, ReasoningDelta))
    assert reasoning == "Let me think."


async def test_think_is_not_a_supported_param(recording, http_client) -> None:
    # ADR-0018: the reasoning toggle is not wired for Anthropic, so it must never
    # appear as something the interface can turn on for this backend.
    provider = _provider(recording[0], http_client)
    assert "think" not in provider.supported_params


async def test_tool_calls_stream_as_fragments(recording, http_client) -> None:
    server, _ = recording
    provider = _provider(server, http_client)
    events = [
        event
        async for event in provider.stream_chat(
            ChatRequest(
                model="tools",
                messages=(ChatMessage(role="user", content="weather in Turin?"),),
            )
        )
    ]
    fragments = [event for event in events if isinstance(event, ToolCallDelta)]
    assert [f.id for f in fragments] == ["toolu_abc", "toolu_abc"]
    assert "".join(f.arguments_fragment for f in fragments) == '{"city": "Turin"}'
    done = events[-1]
    assert isinstance(done, Done) and done.finish_reason == "tool_calls"


async def test_a_tool_result_message_becomes_a_user_turn(recording, http_client) -> None:
    server, requests = recording
    provider = _provider(server, http_client)
    async for _ in provider.stream_chat(
        ChatRequest(
            model="claude-sonnet-5",
            messages=(
                ChatMessage(role="user", content="weather?"),
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="toolu_1", name="get_weather", arguments='{"city": "Turin"}'
                        ),
                    ),
                ),
                ChatMessage(role="tool", content="18C, cloudy", tool_call_id="toolu_1"),
            ),
        )
    ):
        pass
    sent_messages = requests[-1]["messages"]
    tool_result_message = sent_messages[-1]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["type"] == "tool_result"
    assert tool_result_message["content"][0]["tool_use_id"] == "toolu_1"
    assistant_message = sent_messages[-2]
    assert assistant_message["content"][0]["type"] == "tool_use"
    assert assistant_message["content"][0]["id"] == "toolu_1"


async def test_sampling_parameters_reach_the_backend(recording, http_client) -> None:
    server, requests = recording
    provider = _provider(server, http_client)
    async for _ in provider.stream_chat(
        ChatRequest(
            model="claude-sonnet-5",
            messages=(ChatMessage(role="user", content="hi"),),
            params=SamplingParams(
                temperature=0.4, top_p=0.9, top_k=40, stop=("END",), max_tokens=512
            ),
        )
    ):
        pass
    sent = requests[-1]
    assert sent["temperature"] == 0.4
    assert sent["top_p"] == 0.9
    assert sent["top_k"] == 40
    assert sent["stop_sequences"] == ["END"]
    assert sent["max_tokens"] == 512


async def test_missing_model_is_typed(recording, http_client) -> None:
    provider = _provider(recording[0], http_client)
    with pytest.raises(ModelNotFound):
        async for _ in provider.stream_chat(
            ChatRequest(model="missing", messages=(ChatMessage(role="user", content="hi"),))
        ):
            pass


async def test_context_overflow_is_typed(recording, http_client) -> None:
    provider = _provider(recording[0], http_client)
    with pytest.raises(ContextOverflow):
        async for _ in provider.stream_chat(
            ChatRequest(model="ctx", messages=(ChatMessage(role="user", content="hi"),))
        ):
            pass


async def test_rate_limit_carries_retry_after(recording, http_client) -> None:
    provider = _provider(recording[0], http_client)
    with pytest.raises(RateLimited) as excinfo:
        async for _ in provider.stream_chat(
            ChatRequest(model="limited", messages=(ChatMessage(role="user", content="hi"),))
        ):
            pass
    assert excinfo.value.retry_after_s == 5.0


async def test_overloaded_is_treated_like_rate_limited(recording, http_client) -> None:
    provider = _provider(recording[0], http_client)
    with pytest.raises(RateLimited):
        async for _ in provider.stream_chat(
            ChatRequest(model="overloaded", messages=(ChatMessage(role="user", content="hi"),))
        ):
            pass


async def test_bad_credentials_are_typed(http_client) -> None:
    app = create_anthropic(api_key="sk-ant-real")
    with run_fake(app) as server:
        provider = _provider(server, http_client, api_key="sk-ant-wrong")
        with pytest.raises(AuthError):
            async for _ in provider.stream_chat(
                ChatRequest(
                    model="claude-sonnet-5", messages=(ChatMessage(role="user", content="hi"),)
                )
            ):
                pass


async def test_health_reports_up(recording, http_client) -> None:
    provider = _provider(recording[0], http_client)
    health = await provider.health()
    assert health.state == HealthState.UP


async def test_embed_is_unsupported(recording, http_client) -> None:
    from velox_ui.providers.errors import UnsupportedCapability

    provider = _provider(recording[0], http_client)
    with pytest.raises(UnsupportedCapability):
        await provider.embed("claude-sonnet-5", ["hi"])


async def test_credential_never_appears_in_repr(recording, http_client) -> None:
    provider = _provider(recording[0], http_client, api_key="sk-ant-super-secret")
    assert "sk-ant-super-secret" not in repr(provider)
