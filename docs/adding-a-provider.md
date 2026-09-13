# Adding a provider

Adding a backend should cost one file plus one registry entry — or, for anything that
speaks the OpenAI protocol, a single TOML entry and no code at all. If you find
yourself writing `if provider == "..."` anywhere outside `providers/registry.py`, the
abstraction has been bypassed and the change is wrong.

## Decide which of the three paths applies

**1. It speaks the OpenAI protocol.** Add a preset to `providers/presets.toml`. No
Python. This covers vLLM, LM Studio, TGI, TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile,
mlx_lm, Groq, OpenRouter, NVIDIA NIM and OpenAI itself
([ADR-0009](adr/0009-openai-compat-parametrized-adapter.md)).

```toml
[lmstudio]
kind = "openai_compat"
label = "LM Studio"
base_url = "http://localhost:1234/v1"
auth = "none"          # local backends must work without a key (ADR-0008)
local = true
discovery_ports = [1234]
sampling = ["top_k", "min_p", "repeat_penalty"]   # accepted beyond the OpenAI set
docs_url = "https://lmstudio.ai/docs/app/api/endpoints/openai"
```

A backend that spells a parameter differently renames it —
`rename = { repeat_penalty = "repetition_penalty" }` — and only parameters in the OpenAI
set or in `sampling` are ever sent, so a server that rejects unknown fields never sees
one it does not know. The preset is immediately configurable from the environment
(`VELOX_PROVIDER_LMSTUDIO_HOSTS`), selectable in the interface, and, with
`discovery_ports`, found at first start.

**2. It speaks the OpenAI protocol with a deviation nobody has modelled yet.** Add a
quirk flag rather than a new adapter. A quirk is data; a second adapter is a second
streaming loop to keep correct forever.

**3. Its protocol genuinely differs.** Write an adapter. Ollama, llama.cpp, Gemini,
Anthropic, Mistral and Cloudflare's native route qualify; each has something the OpenAI
shape cannot express — model management, GBNF grammars, a different content model.

## Writing an adapter

Create `src/velox_ui/providers/<name>.py` implementing the
[`Provider`](design/04-provider-interface.md) protocol. There is no base class to
inherit: the protocol is structural, so an adapter is a plain object with the right
methods.

```python
class MyProvider:
    is_local = False

    def __init__(self, provider_id: str, base_url: str, client: httpx.AsyncClient) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]: ...
    async def capabilities(self, model: str, *, refresh: bool = False) -> Capabilities: ...
    def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...
    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]: ...
    async def health(self) -> Health: ...
```

Then add one branch to `ProviderRegistry._build`, importing your module inside it. The
import must stay inside the function: a module-level import of every adapter would put
httpx and each backend's dependencies on the startup path of instances that never use
them, which is measured and gated (`bench/cases/cold_start.py`).

### Five rules the contract test suite enforces

**Never buffer the response.** Yield each `TextDelta` as the backend produces it. An
adapter that collects the full reply before returning passes a naive test and fails
every user on a slow host.

**Read capabilities from the backend.** Context window, vision support, tool support,
quantisation: probe them, never infer them from the model's name. Two copies of the
same model can be built with different context lengths, and only the backend knows.

**Cache what does not change.** `capabilities()` is called on the completion path.
Probing it per turn costs a round trip against a 15 ms budget
([ADR-0004](adr/0004-streaming-passthrough.md)).

**Map errors to types, not strings.** Every failure becomes a
`providers.errors.ProviderError` subclass. The distinction that matters most is
`ModelLoading` versus `BackendTimeout`: the first is never retried, because retrying
asks a struggling host to load the same weights a second time
([ADR-0008](adr/0008-local-first-provider-behaviour.md)).

**Report the backend's own metrics.** Put whatever the backend reports — token counts,
prompt-eval time, tokens per second — into `Usage` verbatim. Do not invent numbers it
did not give you, and leave fields `None` when it gave nothing.

### For local backends specifically

Authentication is optional and its absence must produce no warning. An unreachable host
returns `Health(state=DOWN)` and never raises from `health()`. If the backend can
manage stored models, also implement `LocalModelAdmin`; routes discover that with
`isinstance`, so model management appears in the UI without anything else changing.

## Contract tests are not optional

Add a fake under `tests/fakes/` that replays your backend's **real** byte-level
streaming format, and a test module under `tests/contract/`. Not an idealised format —
the real one, including its oddities.

This is the step that catches what review does not. The Ollama adapter passed a full
contract suite and still returned nothing at all for reasoning models, because real
qwen3 streams its output in `message.thinking` while leaving `content` empty, and the
fake did not. It was found by pointing the adapter at a real host. When you discover a
divergence that way, teach the fake first, then fix the adapter — otherwise the next
change reintroduces it.

Cover at least: a normal stream, first-token latency under a slow backend, usage
extraction, each error your backend can produce, and an unreachable host.

## Checklist

- [ ] Preset in `providers/presets.toml`, or an adapter plus one registry branch
- [ ] Adapter module imported lazily, inside the registry branch
- [ ] Capabilities probed and cached
- [ ] Errors mapped to typed `ProviderError` subclasses
- [ ] `health()` never raises
- [ ] Fake replaying the real wire format, under `tests/fakes/`
- [ ] Contract tests under `tests/contract/`
- [ ] Row added to the provider table in `README.md`

## Optional capabilities

Model management is not part of the core contract. An adapter that can report what is
installed and loaded implements `ModelInspector` (`show`, `running`); one that can also
change it implements `LocalModelAdmin` (`pull`, `create`, `delete`, `copy`, `unload`).
Both are structural protocols checked with `isinstance`, and `GET /api/models` turns them
into a `features` list, so the Models page enables controls for a new backend without
anyone touching the interface.

Every adapter also declares `supported_params`: the sampling parameters it will actually
send. The Parameters panel shows exactly these, and saving any other is rejected — a
setting that silently does nothing is worse than one that is not offered.

`pull` and `create` return async iterators of `PullProgress`. They run as server-side
jobs (ADR-0017), so an adapter only has to report progress honestly, including failures
that arrive inside a successful response: Ollama answers a pull of a nonexistent model
with HTTP 200 and an `error` line, and a contract test holds the adapter to that.
