# velox-ui

A fast, self-hosted frontend for local and remote language models. Functional parity
with Open WebUI, built for latency and a small footprint.

**Local backends are the primary case, not a fallback.** No API key is required,
loading a model is reported as its own state rather than as a timeout, and a host that
is switched off degrades to an offline badge instead of an error.

> **Status: phase 10 of 10 — feature-complete.** There is a working product:
> configuration, database, authentication, the conversation tree, the provider
> abstraction with local (Ollama, llama.cpp) and cloud (Groq, OpenRouter, Mistral,
> NVIDIA, Cloudflare, Gemini, Anthropic, OpenAI) adapters, local model management, a
> web interface with streaming, virtual scrolling, markdown and per-reply speed
> metrics, organization (folders, tags, pin/archive), full-text search, custom model
> presets, an admin user console, RAG (knowledge-base collections, document upload and
> ingestion, sqlite-vec/pgvector retrieval, citations streamed into chats whose custom
> model carries `knowledge_ids`), MCP and tool calling (stdio, Streamable HTTP and
> legacy HTTP+SSE MCP servers with transport detection, an approval gate for tool calls, native tool calling on the
> OpenAI-compatible and Anthropic adapters, prompt-based emulation for models without
> native support, and a bounded tool-call loop streamed into chats whose custom model
> carries `tools`), Open WebUI import (`velox import openwebui`, reconstructing the
> branching message tree, tags and folders from Open WebUI's own chat export, see
> [docs/migration-openwebui.md](docs/migration-openwebui.md)), and now optional
> plugins: image generation and voice (transcription/speech), each a thin
> OpenAI-compatible HTTP client disabled by default and never imported when off
> (ADR-0014, ADR-0021). Verified in a real browser against a real Ollama host. The
> design record is in [docs/design/00-overview.md](docs/design/00-overview.md);
> nothing below is claimed to work unless it is marked as shipped.

## Quickstart

```bash
docker run -d -p 8080:8080 -v velox-data:/data --name velox-ui velox-ui:latest
```

Open <http://localhost:8080> and create the first account — it becomes the
administrator. That is the whole setup: no database to provision, no API key to enter,
no configuration file to write.

From a source checkout:

```bash
uv venv && uv pip install -e '.[dev]'   # or: pip install -e '.[dev]'
velox serve
```

## Why another one

Open WebUI works, but it is heavy: hundreds of megabytes of image, around a gigabyte of
resident memory at idle, seconds of cold start, and hundreds of milliseconds between
the backend producing a token and the screen showing it. velox-ui is a rewrite, not a
fork, with an architecture built around that last number.

| Target | Budget | Measured today |
|---|---|---|
| Time-to-first-token overhead vs a direct backend call | < 15 ms p95 | **10.4 ms** |
| Cold start | < 1 s | **0.88 s** |
| Resident memory, idle | < 150 MB | **128 MB** |
| Container image | < 250 MB | **186 MB** |
| Open a 5 000-message conversation | < 150 ms | **9.8 ms** (over HTTP; see note) |
| List 10 000 conversations | < 30 ms | **2.0 ms** |
| Frontend bundle | < 200 KB gzip | **37.3 KB** |

Measured on a 4-core x86-64 Linux host with `python -m bench`, against deterministic
local fixtures — no GPU, no network, no API key. Reproduce them yourself; the suite
runs in CI on every change and a regression fails the build (ADR-0015).
[docs/benchmarks.md](docs/benchmarks.md) has the full table and how to read it.

Two of those numbers need their caveat stated rather than buried. The
time-to-first-token figure is a *difference*: the same request is timed straight to the
backend and through velox-ui over the same loopback socket, and 10.4 ms is what
separates them — an absolute number there would mostly measure the model. The
5 000-message figure is the whole HTTP request that opens the conversation, which
returns its newest page rather than all of it: the interface virtualises and fetches
earlier pages as the reader scrolls up. Reading the entire history back takes about
0.4 s over 25 pages, and the benchmark prints that alongside the headline.

## How it is built

- **Backend**: Python 3.12, FastAPI, fully async. msgspec on the hot path, Pydantic
  confined to cold routes — a boundary enforced by a test, not by good intentions
  ([ADR-0001](docs/adr/0001-fastapi-with-msgspec.md)).
- **Server**: Granian by default, uvicorn supported.
- **Database**: SQLite in WAL mode, zero configuration; PostgreSQL optional. Explicit
  indexes, keyset pagination, no `SELECT *` on messages.
- **Streaming**: byte-level pass-through. Where a backend's wire format already matches
  ours, upstream bytes are forwarded untouched and a cheap scanner tees off what
  persistence and metrics need. The response is never accumulated in memory
  ([ADR-0004](docs/adr/0004-streaming-passthrough.md)).
- **Persistence beside the stream, never in front of it**: identifiers are generated
  in-process, so the first token can reach the client before the row lands
  ([ADR-0005](docs/adr/0005-persistence-off-the-hot-path.md)).
- **Conversations are trees**: editing or regenerating adds a branch and never
  overwrites history ([ADR-0006](docs/adr/0006-message-tree-branching.md)).
- **Frontend**: Svelte 5 runes, no component library, no router. Messages render
  through a variable-height virtual list, so a five-thousand-message conversation puts
  a dozen nodes in the DOM. Streaming tokens land in a buffer published once per
  animation frame, updating one text node rather than the conversation. Markdown,
  syntax highlighting and sanitisation run in a web worker, on completed blocks only
  ([ADR-0012](docs/adr/0012-svelte5-virtual-scroll-worker.md)).
- **Model output is never trusted**: raw HTML is escaped before the markdown parser
  sees it, every link scheme is checked, and the Content-Security-Policy allows no
  inline or evaluated script. A prompt injection that reaches the renderer still
  cannot run code.

## Providers

One adapter contract, not a collection of special cases. Backends that speak the
OpenAI protocol — every cloud provider below but Anthropic — share a single
parametrized adapter driven by a preset file, so adding one is a TOML entry rather than
code ([ADR-0009](docs/adr/0009-openai-compat-parametrized-adapter.md)). Anthropic's
Messages API does not fit that shape and gets its own adapter
([ADR-0018](docs/adr/0018-anthropic-dedicated-adapter.md)).

| Provider | Adapter | Status |
|---|---|---|
| Ollama | native `/api/*` | **shipped** |
| llama.cpp / `llama-server` | native, with GBNF grammars and prompt-cache reuse | **shipped** |
| vLLM, LM Studio, TGI, TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile, mlx_lm, Text Generation WebUI | OpenAI-compatible preset | **shipped** |
| OpenAI, Groq, OpenRouter, Mistral, NVIDIA NIM, Cloudflare Workers AI, Google Gemini | OpenAI-compatible preset | **shipped** |
| Anthropic | native `/v1/messages` | **shipped** |
| Custom OpenAI-compatible | manual | **shipped** |

The contract, the capability model and the error taxonomy are specified in
[docs/design/04-provider-interface.md](docs/design/04-provider-interface.md), and
[docs/adding-a-provider.md](docs/adding-a-provider.md) walks through adding one.
[docs/providers.md](docs/providers.md) has the full backend-by-backend table.

What the two shipped adapters do beyond streaming text: real context windows and
capabilities read from `/api/show` and `/props` rather than guessed from model names;
model loading reported as its own state instead of as a stall; reasoning models'
separated thinking channel surfaced as its own event; the full sampling parameter set
(`num_ctx`, `num_gpu`, `mirostat`, `min_p`, GBNF grammars, `cache_prompt`) passed
through; and the backend's own token counts and timings reported verbatim, with cost
stated as exactly zero for local models.

### Managing local models

The **Models** page shows what each backend has loaded — memory, how much of it is on the
GPU, when it will unload — and what is installed. On Ollama it also downloads models with
live progress, creates them from a Modelfile, and copies, deletes and unloads them.
Downloads run on the server, so closing the page does not cancel one
([ADR-0017](docs/adr/0017-model-jobs-run-on-the-server.md)).

**Parameters**, next to the model picker, saves per-model settings such as `num_ctx`,
`num_gpu`, `keep_alive` or `mirostat` and applies them to every message. It shows only
what the model's backend accepts, with the model's own defaults as placeholders.

### Pointing velox-ui at a backend

```bash
VELOX_PROVIDER_OLLAMA_HOSTS=http://192.168.1.10:11434 velox serve
```

Several hosts are comma-separated, and `VELOX_PROVIDER_LLAMACPP_HOSTS` works the same
way. Every other backend is configured from its preset — `VELOX_PROVIDER_LMSTUDIO_HOSTS`,
`VELOX_PROVIDER_VLLM_HOSTS`, `VELOX_PROVIDER_VLLM_API_KEY` — or added in the interface
under **Providers**, which tests the address first, stores the key encrypted and only
ever shows it masked. Configure nothing and velox-ui probes the conventional local ports at startup and
uses whatever answers — loopback only, in the background, never the LAN.

## Configuration

velox-ui runs with none. Every setting has a working default, and anything can be set
in `velox.toml` or as an environment variable — `VELOX_PORT`, `VELOX_DB_URL`,
`VELOX_AUTH_ENABLED` — with the environment always winning. See
[velox.example.toml](velox.example.toml) for the annotated reference, and run
`velox config check` to validate what an instance would actually use (credentials are
masked, so the output is safe to paste into a bug report). The full reference, section
by section, is [docs/configuration.md](docs/configuration.md);
[docs/deployment.md](docs/deployment.md) covers Docker, PostgreSQL, a reverse proxy and
backups. Running an npm- or PyPI-published MCP server alongside a containerised
velox-ui needs the companion gateway image, since the runtime carries no Node and no
package manager: [docs/mcp-gateway.md](docs/mcp-gateway.md).

## Commands

```
velox serve                  start the server
velox config check           validate and print the effective configuration
velox migrate up|current     apply or inspect database migrations
velox bench                  run the performance suite
```

## Development

```bash
uv venv && uv pip install -e '.[dev,postgres,uvicorn]'
ruff check . && ruff format --check .
mypy
pytest
python -m bench

# The interface. The build output lands in src/velox_ui/web, which the server serves
# and the wheel ships, so there is no Node at runtime.
npm --prefix frontend install
npm --prefix frontend run check    # svelte-check
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run size     # enforces the bundle budget
```

Running without building the interface is fine: the server says so at `/` and serves
its API normally.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the fuller version of this, including how to
add a provider or a plugin, and [CHANGELOG.md](CHANGELOG.md) for what shipped in each
phase.

No test requires an API key, a network or a real inference backend, and none ever will:
provider adapters are verified against fake servers that replay each backend's real
streaming format, over real sockets so that assertions about *when* bytes arrive mean
something.

That said, fakes only contain the behaviour someone thought to put in them. The Ollama
adapter passed its whole contract suite and still returned nothing for reasoning
models, because real qwen3 streams into `message.thinking` while leaving `content`
empty. Point an adapter at real hardware before believing it.

## License

Apache-2.0. See [LICENSE](LICENSE).
