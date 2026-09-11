# velox-ui

A fast, self-hosted frontend for local and remote language models. Functional parity
with Open WebUI, built for latency and a small footprint.

**Local backends are the primary case, not a fallback.** No API key is required,
loading a model is reported as its own state rather than as a timeout, and a host that
is switched off degrades to an offline badge instead of an error.

> **Status: phase 1 of 10.** The foundation is in place and measured — configuration,
> database, migrations, authentication, the conversation store and the benchmark gates.
> The provider layer, chat streaming and the web interface land in phases 2 and 3. The
> plan is in [docs/design/00-overview.md](docs/design/00-overview.md); nothing below is
> claimed to work unless it is marked as shipped.

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
| Time-to-first-token overhead vs a direct backend call | < 15 ms p95 | phase 2 |
| Cold start | < 1 s | **0.89 s** |
| Resident memory, idle | < 150 MB | **118 MB** |
| Container image | < 250 MB | **186 MB** |
| Open a 5 000-message conversation | < 150 ms | **104 ms** (storage half; see note) |
| List 10 000 conversations | < 30 ms | **1.7 ms** |
| Frontend bundle | < 200 KB gzip | phase 3 |

Measured on a 4-core x86-64 Linux host with `python -m bench`, against deterministic
local fixtures — no GPU, no network, no API key. Reproduce them yourself; the suite
runs in CI on every change and a regression fails the build (ADR-0015). The
5 000-message figure currently covers the database query and tree assembly; the HTTP
and rendering halves join it in phases 2 and 3.

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
| Ollama | native `/api/*` | phase 2 |
| llama.cpp / `llama-server` | native, with GBNF grammars and prompt-cache reuse | phase 2 |
| vLLM, LM Studio, TGI, TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile, mlx_lm | OpenAI-compatible preset | phase 4 |
| Groq, OpenRouter, NVIDIA NIM, OpenAI | OpenAI-compatible preset | phase 5 |
| Google Gemini, Anthropic, Mistral, Cloudflare Workers AI | native | phase 5 |
| Custom OpenAI-compatible | manual | phase 4 |

The contract, the capability model and the error taxonomy are specified in
[docs/design/04-provider-interface.md](docs/design/04-provider-interface.md); adding one
is documented in `docs/adding-a-provider.md` when the layer ships.

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
streaming format.

## License

Apache-2.0. See [LICENSE](LICENSE).
