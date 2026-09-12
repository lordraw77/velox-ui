"""The provider contract.

Shared structs, the streaming event union, and the Protocol every adapter implements
(docs/design/04-provider-interface.md).

Everything here is a frozen ``msgspec.Struct``. That is not a style preference: these
types are built and torn down once per token on the streaming path, and a dict-based or
Pydantic-based representation would mean an allocation and a validation pass the
15 ms TTFT budget cannot afford (ADR-0001, ADR-0004).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

import msgspec

__all__ = [
    "Capabilities",
    "ChatMessage",
    "ChatRequest",
    "ContentPart",
    "Done",
    "Health",
    "HealthState",
    "LocalModelAdmin",
    "ModelInfo",
    "Provider",
    "PullProgress",
    "ReasoningDelta",
    "RunningModel",
    "SamplingParams",
    "Status",
    "StatusPhase",
    "StreamEvent",
    "TextDelta",
    "Timeouts",
    "ToolCall",
    "ToolCallDelta",
    "ToolChoice",
    "ToolSpec",
    "ToolSupport",
    "Usage",
]


class ToolSupport(StrEnum):
    """How a model handles tool calling."""

    NATIVE = "native"
    EMULATED = "emulated"
    NONE = "none"


class HealthState(StrEnum):
    """Reachability of a configured backend."""

    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class StatusPhase(StrEnum):
    """What a backend is doing before or between tokens.

    The distinction between ``loading_model`` and ``generating`` is the whole point of
    ADR-0008: a local model that takes forty seconds to load from disk is not a hung
    backend, and the UI must be able to tell the two apart.
    """

    LOADING_MODEL = "loading_model"
    PROMPT_EVAL = "prompt_eval"
    GENERATING = "generating"
    TOOL_WAIT = "tool_wait"


class Timeouts(msgspec.Struct, frozen=True):
    """Per-request timeout budget.

    Attributes:
        connect_s: TCP/TLS connect budget.
        first_token_s: Budget for the first byte of the response. Generous by
            default, because loading a local model from disk can take minutes.
        between_tokens_s: Budget between successive chunks once streaming has begun.
        total_s: Overall budget, or ``None`` for no cap. Unset by default: a long
            generation on a slow local host is legitimate, not a hang.
    """

    connect_s: float = 3.0
    first_token_s: float = 600.0
    between_tokens_s: float = 120.0
    total_s: float | None = None


class Capabilities(msgspec.Struct, frozen=True):
    """What a model can do, as reported by its backend — never guessed from its name."""

    streaming: bool = True
    tools: ToolSupport = ToolSupport.NONE
    vision: bool = False
    audio_in: bool = False
    json_mode: bool = False
    grammar: bool = False
    reasoning: bool = False
    embeddings: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None
    price_in_ppm: int | None = None
    price_out_ppm: int | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    chat_template: str | None = None


class ModelInfo(msgspec.Struct, frozen=True):
    """One model as discovered from a provider."""

    key: str
    display_name: str
    provider_id: str
    capabilities: Capabilities
    family: str | None = None
    loaded: bool | None = None


class Health(msgspec.Struct, frozen=True):
    """The outcome of a reachability probe. Never raises; a down host is a normal state."""

    state: HealthState
    latency_ms: float | None = None
    detail: str | None = None


class ContentPart(msgspec.Struct, frozen=True):
    """One part of a multimodal message."""

    kind: Literal["text", "image"]
    text: str | None = None
    image_url: str | None = None
    image_base64: str | None = None


class ToolCall(msgspec.Struct, frozen=True):
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: str  # JSON-encoded, as models actually emit it


class ToolSpec(msgspec.Struct, frozen=True):
    """A tool definition offered to the model."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolChoice(msgspec.Struct, frozen=True):
    """Constrains which tool, if any, the model must call."""

    mode: Literal["auto", "none", "required", "named"] = "auto"
    name: str | None = None


class ChatMessage(msgspec.Struct, frozen=True):
    """One turn of conversation, in the wire-neutral shape adapters translate from."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[ContentPart, ...]
    name: str | None = None
    tool_calls: tuple[ToolCall, ...] | None = None
    tool_call_id: str | None = None


class SamplingParams(msgspec.Struct, frozen=True):
    """Superset of sampling parameters. Each adapter maps what its backend supports."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    typical_p: float | None = None
    tfs_z: float | None = None
    repeat_penalty: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    mirostat: int | None = None
    mirostat_tau: float | None = None
    mirostat_eta: float | None = None
    penalize_nl: bool | None = None
    seed: int | None = None
    stop: tuple[str, ...] | None = None
    max_tokens: int | None = None
    num_ctx: int | None = None
    num_gpu: int | None = None
    num_thread: int | None = None
    num_batch: int | None = None
    keep_alive: str | None = None
    cache_prompt: bool | None = None


class ChatRequest(msgspec.Struct, frozen=True):
    """A completion request, in the wire-neutral shape adapters translate from."""

    model: str
    messages: tuple[ChatMessage, ...]
    params: SamplingParams = msgspec.field(default_factory=SamplingParams)
    tools: tuple[ToolSpec, ...] | None = None
    tool_choice: ToolChoice | None = None
    grammar: str | None = None
    json_schema: dict[str, Any] | None = None
    stream: bool = True
    extra: dict[str, Any] = msgspec.field(default_factory=dict)


# --- Streaming event union -------------------------------------------------------
#
# Each variant maps 1:1 onto an SSE event of the public API (docs/design/03-http-api.md),
# so the service layer forwards without translating.


class Status(msgspec.Struct, frozen=True, tag="status", tag_field="type"):
    """A phase change that is not itself text."""

    phase: StatusPhase


class TextDelta(msgspec.Struct, frozen=True, tag="delta", tag_field="type"):
    """A chunk of assistant text. The hot-path variant."""

    text: str


class ReasoningDelta(msgspec.Struct, frozen=True, tag="reasoning", tag_field="type"):
    """A chunk of separated model "thinking", for models that emit it."""

    text: str


class ToolCallDelta(msgspec.Struct, frozen=True, tag="tool_call", tag_field="type"):
    """Partial or complete tool-call arguments."""

    id: str
    name: str
    arguments_fragment: str


class Usage(msgspec.Struct, frozen=True, tag="usage", tag_field="type"):
    """Token accounting and timing, reported once at the end of a stream."""

    tokens_in: int
    tokens_out: int
    ttft_ms: float | None = None
    tokens_per_second: float | None = None
    prompt_eval_ms: float | None = None
    eval_ms: float | None = None
    cost_micros: int = 0


class Done(msgspec.Struct, frozen=True, tag="done", tag_field="type"):
    """Terminates the stream."""

    finish_reason: Literal["stop", "length", "tool_calls", "error", "cancelled"]


type StreamEvent = Status | TextDelta | ReasoningDelta | ToolCallDelta | Usage | Done


class PullProgress(msgspec.Struct, frozen=True):
    """Progress of a local model download."""

    status: str
    completed_bytes: int | None = None
    total_bytes: int | None = None
    digest: str | None = None


class RunningModel(msgspec.Struct, frozen=True):
    """A model currently loaded in a local backend's memory."""

    name: str
    size_bytes: int | None = None
    vram_bytes: int | None = None
    expires_at_ms: int | None = None


@runtime_checkable
class Provider(Protocol):
    """One configured backend instance."""

    provider_id: str
    is_local: bool

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """Discover models. Cached by the caller; ``refresh`` bypasses that cache."""
        ...

    async def capabilities(self, model: str, *, refresh: bool = False) -> Capabilities:
        """Probe real capabilities for one model.

        Adapters are expected to cache this: it is called on the completion path, and
        a model's context window and template do not change while it exists. The
        ``refresh`` flag exists for the UI's explicit refresh action.
        """
        ...

    def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream one completion.

        Must yield the first ``TextDelta`` as soon as the backend produces it, must
        never buffer the full response, and must translate backend failures into
        :class:`~velox_ui.providers.errors.ProviderError` subclasses.
        """
        ...

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts.

        Raises:
            UnsupportedCapability: If the backend or model cannot produce embeddings.
        """
        ...

    async def health(self) -> Health:
        """Cheap reachability probe with a short timeout. Never raises."""
        ...


@runtime_checkable
class LocalModelAdmin(Protocol):
    """Optional mixin implemented by adapters that manage local model storage.

    Routes query this with ``isinstance`` rather than by provider name, so a new local
    backend gains model management by implementing it and nothing else changes.
    """

    def pull(self, name: str) -> AsyncIterator[PullProgress]:
        """Download a model, yielding progress as it goes."""
        ...

    async def delete(self, name: str) -> None:
        """Remove a model from local storage."""
        ...

    async def copy(self, src: str, dst: str) -> None:
        """Copy a model under a new name."""
        ...

    async def running(self) -> list[RunningModel]:
        """List the models currently held in memory."""
        ...

    async def unload(self, name: str) -> None:
        """Evict a model from memory."""
        ...
