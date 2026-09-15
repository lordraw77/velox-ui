# Providers

Every backend velox-ui talks to is one of two adapters: the native `ollama` and
`llamacpp` adapters, the dedicated `anthropic` adapter (ADR-0018), or the
parametrized OpenAI-compatible adapter (ADR-0009) driven by a preset in
`src/velox_ui/providers/presets.toml`. Local backends sort first and need no
credential (ADR-0008); a backend that cannot be reached is reported as offline
rather than failing the request it's asked for.

Add a backend from the interface under **Providers**, or in `velox.toml`'s
`[[providers.endpoints]]` (see `velox.example.toml`). The full mechanism,
including how to add a new preset or a genuinely new adapter, is in
`docs/adding-a-provider.md`.

## Local

| Backend | Adapter | Default address | Credential |
|---|---|---|---|
| Ollama | native | `http://localhost:11434` | none |
| llama.cpp (`llama-server`) | native | `http://localhost:8080` | optional |
| LM Studio | OpenAI-compatible | `http://localhost:1234/v1` | none |
| vLLM | OpenAI-compatible | `http://localhost:8000/v1` | optional |
| Text Generation Inference | OpenAI-compatible | `http://localhost:8080/v1` | optional |
| TabbyAPI | OpenAI-compatible | see preset | optional |
| KoboldCpp | OpenAI-compatible | see preset | optional |
| LocalAI | OpenAI-compatible | see preset | optional |
| Jan | OpenAI-compatible | see preset | optional |
| llamafile | OpenAI-compatible | see preset | optional |
| MLX LM | OpenAI-compatible | see preset | optional |
| Text Generation WebUI | OpenAI-compatible | see preset | optional |
| Custom OpenAI-compatible server | OpenAI-compatible | none (you set it) | optional |

Local backends are autodiscovered at startup by probing the conventional
loopback ports above — never the LAN — unless autodiscovery is disabled
(`providers.autodiscover = false`).

## Cloud

| Backend | Adapter | Credential |
|---|---|---|
| OpenAI | OpenAI-compatible | required |
| Groq | OpenAI-compatible | required |
| OpenRouter | OpenAI-compatible | required |
| Mistral | OpenAI-compatible | required |
| NVIDIA | OpenAI-compatible | required |
| Cloudflare Workers AI | OpenAI-compatible | required (`base_url` too — it carries your account id) |
| Google Gemini | OpenAI-compatible | required |
| Anthropic | dedicated adapter (`providers/anthropic.py`) | required |

Seven of the eight cloud backends speak the OpenAI protocol and are presets
over the same adapter local backends use. Anthropic does not, and gets its
own adapter (ADR-0018) rather than a compatibility shim.

Cloud credentials entered through the interface are encrypted at rest
(ADR-0013); an environment variable is the alternative for a headless
deployment: `VELOX_PROVIDER_<PRESET>_API_KEY` (e.g.
`VELOX_PROVIDER_ANTHROPIC_API_KEY`).

## Capabilities

Every adapter reports what it can do rather than the interface guessing from
its name — streaming, native tool calling versus prompt-emulated (phase 8),
vision, and, for local backends, model management (`show`, `running`, `pull`,
`create`, `delete`, `copy`, `unload`). `GET /api/models` and `GET
/api/providers` surface this per model and per backend; the interface enables
controls from it instead of hardcoding which backends support what.

## Optional plugins

Image generation, voice (STT/TTS) and builtin tools are separate from chat
providers — they are entry-point plugins (ADR-0014), disabled by default,
configured under **Plugins**. Images and voice are OpenAI-compatible HTTP
clients (ADR-0021). The `tools` group holds the tools a chat model can call
mid-turn: `current_datetime` needs nothing configured, while `web_search` and
`web_browse` run over a self-hosted SearXNG instance.
See `docs/configuration.md`.
