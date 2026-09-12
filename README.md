# velox-ui

A fast, self-hosted frontend for local and remote language models. Functional parity
with Open WebUI, built for latency and a small footprint.

**Local backends are the primary case, not a fallback.** No API key is required,
loading a model is reported as its own state rather than as a timeout, and a host that
is switched off degrades to an offline badge instead of an error.

> **Status: phase 2 of 10.** The foundation and the inference path are in place and
> measured: configuration, database, authentication, the conversation tree, the
> provider abstraction, and complete Ollama and llama.cpp adapters with streaming,
> branching and persistence. Verified against both fake backends and a real Ollama
> host. There is no web interface yet — that is phase 3, and until then the API is
> the product. The plan is in [docs/design/00-overview.md](docs/design/00-overview.md);
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
| Time-to-first-token overhead vs a direct backend call | < 15 ms p95 | **10.2 ms** |
| Cold start | < 1 s | **0.92 s** |
| Resident memory, idle | < 150 MB | **127 MB** |
| Container image | < 250 MB | **186 MB** |
| Open a 5 000-message conversation | < 150 ms | **103 ms** (storage half; see note) |
| List 10 000 conversations | < 30 ms | **1.6 ms** |
| Frontend bundle | < 200 KB gzip | phase 3 |

Measured on a 4-core x86-64 Linux host with `python -m bench`, against deterministic
local fixtures — no GPU, no network, no API key. Reproduce them yourself; the suite
runs in CI on every change and a regression fails the build (ADR-0015).

Two of those numbers need their caveat stated rather than buried. The
time-to-first-token figure is a *difference*: the same request is timed straight to the
backend and through velox-ui over the same loopback socket, and 10.2 ms is what
separates them — an absolute number there would mostly measure the model. The
5 000-message figure covers the database query and tree assembly only; the rendering
half joins it in phase 3.

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

## Providers

One adapter contract, not a collection of special cases. Backends that speak the
OpenAI protocol share a single parametrized adapter driven by a preset file, so adding
one is a TOML entry rather than code
([ADR-0009](docs/adr/0009-openai-compat-parametrized-adapter.md)).

| Provider | Adapter | Status |
|---|---|---|
| Ollama | native `/api/*` | **shipped** |
| llama.cpp / `llama-server` | native, with GBNF grammars and prompt-cache reuse | **shipped** |
| vLLM, LM Studio, TGI, TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile, mlx_lm | OpenAI-compatible preset | phase 4 |
| Groq, OpenRouter, NVIDIA NIM, OpenAI | OpenAI-compatible preset | phase 5 |
| Google Gemini, Anthropic, Mistral, Cloudflare Workers AI | native | phase 5 |
| Custom OpenAI-compatible | manual | phase 4 |

The contract, the capability model and the error taxonomy are specified in
[docs/design/04-provider-interface.md](docs/design/04-provider-interface.md), and
[docs/adding-a-provider.md](docs/adding-a-provider.md) walks through adding one.

What the two shipped adapters do beyond streaming text: real context windows and
capabilities read from `/api/show` and `/props` rather than guessed from model names;
model loading reported as its own state instead of as a stall; reasoning models'
separated thinking channel surfaced as its own event; the full sampling parameter set
(`num_ctx`, `num_gpu`, `mirostat`, `min_p`, GBNF grammars, `cache_prompt`) passed
through; and the backend's own token counts and timings reported verbatim, with cost
stated as exactly zero for local models.

### Pointing velox-ui at a backend

```bash
VELOX_PROVIDERS_OLLAMA_HOSTS=http://192.168.1.10:11434 velox serve
```

Several hosts are comma-separated, and `VELOX_PROVIDERS_LLAMACPP_HOSTS` works the same
way. Configure nothing and velox-ui probes the conventional local ports at startup and
uses whatever answers — loopback only, in the background, never the LAN.

## Configuration

velox-ui runs with none. Every setting has a working default, and anything can be set
in `velox.toml` or as an environment variable — `VELOX_PORT`, `VELOX_DB_URL`,
`VELOX_AUTH_ENABLED` — with the environment always winning. See
[velox.example.toml](velox.example.toml) for the annotated reference, and run
`velox config check` to validate what an instance would actually use (credentials are
masked, so the output is safe to paste into a bug report).

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
```

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
