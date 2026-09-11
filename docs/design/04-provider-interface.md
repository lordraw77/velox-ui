# Provider abstraction

Design document. One contract, many adapters. No `if provider == "..."` anywhere
outside `providers/registry.py`.

## Data model

All structs are `msgspec.Struct` (frozen where sensible): no Pydantic validation in the
streaming loop.

```python
class ProviderKind(str, Enum):
    OLLAMA = "ollama"
    LLAMACPP = "llamacpp"
    OPENAI_COMPAT = "openai_compat"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    MISTRAL = "mistral"
    CLOUDFLARE = "cloudflare"


class ProviderConfig(Struct, frozen=True):
    """Everything an adapter needs to talk to one backend instance."""

    id: str
    kind: ProviderKind
    base_url: str
    preset: str | None = None
    api_key: str | None = None  # decrypted in memory only; never logged
    extra: dict[str, str] = {}  # account_id, org, project, custom headers
    timeouts: Timeouts = Timeouts()
    is_local: bool = False


class Timeouts(Struct, frozen=True):
    """Separate budgets, because a cold local model is not a hung backend."""

    connect_s: float = 3.0
    first_token_s: float = 600.0  # generous: model load can take minutes
    between_tokens_s: float = 120.0  # 1 tok/s hosts must not trip this
    total_s: float | None = None  # unset: a long generation is legitimate


class Capabilities(Struct, frozen=True):
    streaming: bool = True
    tools: ToolSupport = ToolSupport.NONE  # NATIVE | EMULATED | NONE
    vision: bool = False
    audio_in: bool = False
    json_mode: bool = False
    grammar: bool = False  # GBNF / structured output
    reasoning: bool = False  # separate thinking channel
    embeddings: bool = False
    context_window: int | None = None  # probed from the backend, never by name
    max_output_tokens: int | None = None
    price_in_ppm: int | None = None  # micro-cents / 1M tokens; None for local
    price_out_ppm: int | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    chat_template: str | None = None


class ModelInfo(Struct, frozen=True):
    key: str  # id as the backend names it
    display_name: str
    provider_id: str
    capabilities: Capabilities
    family: str | None = None
    loaded: bool | None = None  # from /api/ps or /slots; None if unknown


class ChatMessage(Struct, frozen=True):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart]  # parts carry images/audio
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class ChatRequest(Struct, frozen=True):
    model: str
    messages: list[ChatMessage]
    params: SamplingParams = SamplingParams()
    tools: list[ToolSpec] | None = None
    tool_choice: str | None = None
    grammar: str | None = None  # GBNF, llama.cpp only
    json_schema: dict | None = None
    stream: bool = True
    extra: dict[str, Any] = {}  # adapter-specific escape hatch (num_gpu, ...)


class SamplingParams(Struct, frozen=True):
    """Superset; each adapter maps what it supports and ignores the rest,
    reporting dropped keys once per model through `unsupported_params`."""

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
    stop: list[str] | None = None
    max_tokens: int | None = None
    num_ctx: int | None = None  # local backends
    num_gpu: int | None = None
    num_thread: int | None = None
    num_batch: int | None = None
    keep_alive: str | None = None  # Ollama
    cache_prompt: bool | None = None  # llama.cpp, default True
```

### Stream events

The adapter yields a small closed union. Every variant maps 1:1 onto an SSE event of
the public API, so the service layer forwards without translating.

```python
StreamEvent = (
    Status  # phase: loading_model | prompt_eval | generating
    | TextDelta  # t: str        -- hot path
    | ReasoningDelta
    | ToolCallDelta  # partial function-call arguments
    | Usage  # tokens, ttft_ms, tok_per_s, prompt_eval_ms, eval_ms
    | Done  # finish_reason
)
```

`TextDelta` carries the already-escaped JSON fragment when the adapter can produce it
without a round-trip (see ADR-0004), otherwise plain text.

## The contract

```python
class Provider(Protocol):
    """One configured backend instance."""

    config: ProviderConfig

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """Discover models. Cached by the registry; `refresh` bypasses the cache."""

    async def capabilities(self, model: str) -> Capabilities:
        """Probe real capabilities for one model (context window, vision, template)."""

    def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream one completion. Must yield the first TextDelta as soon as the
        backend produces it, must not buffer the full response, and must translate
        backend errors into ProviderError subclasses."""

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts. Raises UnsupportedCapability when the backend cannot."""

    async def health(self) -> Health:
        """Cheap reachability probe with a short timeout. Never raises; an
        unreachable local host is a normal state, reported as Health(state=DOWN)."""
```

Optional capability mixins, queried with `isinstance`/`hasattr` by the routes that
need them — never by provider name:

```python
class LocalModelAdmin(Protocol):
    """Implemented by ollama and llamacpp adapters."""

    def pull(self, name: str) -> AsyncIterator[PullProgress]: ...
    async def delete(self, name: str) -> None: ...
    async def copy(self, src: str, dst: str) -> None: ...
    async def create(self, name: str, modelfile: str) -> AsyncIterator[PullProgress]: ...
    async def running(self) -> list[RunningModel]: ...  # /api/ps, /slots
    async def unload(self, name: str) -> None: ...


class RawPassthrough(Protocol):
    """Adapters whose wire format already matches our SSE delta frames can expose
    the upstream byte stream, letting the service tee instead of re-encode."""

    def stream_chat_raw(self, request: ChatRequest) -> AsyncIterator[bytes]: ...
```

## Errors

```python
ProviderError                     # base, carries code, provider, model, retryable
├── AuthError                     # invalid_credentials
├── RateLimited                   # retry_after_s from Retry-After
├── QuotaExceeded
├── ContextOverflow               # includes the real window when known
├── ModelNotFound
├── BackendOffline                # connection refused / DNS / host down
├── BackendTimeout
├── ModelLoading                  # local backend is loading weights; NEVER retried
├── OutOfMemory                   # VRAM/RAM exhaustion on a local backend
├── UnsupportedCapability
└── UpstreamError                 # anything else, with the upstream body attached
```

Retry policy lives in `services/routing.py`, not in the adapters: exponential backoff
with jitter, honoring `Retry-After`, only for `RateLimited` and transient
`UpstreamError`, only before the first byte has been sent to the client, and never for
`ModelLoading` (a retry would trigger a second load of the same weights).

## Registry and presets

```python
def build_provider(cfg: ProviderConfig) -> Provider: ...
```

`registry.py` maps `ProviderKind` to a factory and merges factories contributed by
plugins through the `velox_ui.providers` entry-point group. Cloud providers that speak
OpenAI's protocol (OpenAI, Groq, OpenRouter, NVIDIA, and Cloudflare's compatible route)
are *presets* over `openai_compat`, not separate adapters:

```toml
# providers/presets.toml (excerpt)
[groq]
kind = "openai_compat"
label = "Groq"
base_url = "https://api.groq.com/openai/v1"
auth = "bearer"
capabilities = { tools = "native", json_mode = true }
quirks = { usage_in_final_chunk = true }

[ollama]
kind = "ollama"
label = "Ollama"
base_url = "http://localhost:11434"
auth = "none"
local = true
discovery_ports = [11434]

[lmstudio]
kind = "openai_compat"
label = "LM Studio"
base_url = "http://localhost:1234/v1"
auth = "none"
local = true
discovery_ports = [1234]
quirks = { no_usage_chunk = true, ignores_stop_array = false }
```

Adding a provider is one file in `providers/` plus one registry entry, or just a
preset entry when it is OpenAI-compatible — documented in `docs/adding-a-provider.md`.

## Contract tests

Every adapter runs the same parametrized suite in `tests/contract/` against a fake
server that replays a recorded byte-level transcript of that backend's streaming
format (including its quirks: Ollama's newline-delimited JSON, llama.cpp's `data:`
frames and final `timings`, Gemini's `streamGenerateContent` array chunking,
Anthropic's `content_block_delta` events, Groq's usage-only final chunk). The suite
asserts: first delta before any buffering, correct usage extraction, error mapping,
cancellation propagation, and that no response body is ever fully accumulated.
