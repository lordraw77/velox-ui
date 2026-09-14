# velox-ui — design overview

A self-hosted frontend for local and remote LLMs. Functional parity with Open WebUI,
built for latency and small footprint. Local backends (Ollama, llama.cpp) are the
primary use case, not a fallback.

## Documents

| Document | Contents |
|---|---|
| [01-repo-layout.md](01-repo-layout.md) | Repository tree, layering rules, import budget |
| [02-db-schema.md](02-db-schema.md) | Tables, indexes, pagination and FTS strategy |
| [03-http-api.md](03-http-api.md) | Endpoints, the streaming event protocol, error envelope |
| [04-provider-interface.md](04-provider-interface.md) | Provider contract, structs, errors, presets |
| [../adr/](../adr/) | Architecture decision records 0001-0016 |

> Framework and stack decisions were confirmed at review: FastAPI + msgspec, Granian,
> Svelte 5, Apache-2.0, SQLite/WAL default with optional PostgreSQL.

## Naming

| Thing | Value |
|---|---|
| Project / repository | `velox-ui` |
| PyPI package | `velox-ui` |
| Python module | `velox_ui` |
| CLI entry point | `velox` |
| Container image / compose service | `velox-ui` |
| Environment prefix | `VELOX_` |
| Config file | `velox.toml` |
| Default port | `8080` |

## Performance budget

| Target | Value | Verified by |
|---|---|---|
| TTFT overhead vs direct backend call | < 15 ms p95 | `bench/cases/ttft_overhead.py` |
| Cold start | < 1 s | `bench/cases/cold_start.py` |
| Idle RSS | < 150 MB | `bench/cases/rss_idle.py` |
| Docker image | < 250 MB | `bench/cases/image_size.py` |
| Open a 5 000-message chat | < 150 ms end-to-end | `bench/cases/open_chat_5k.py` |
| Chat list over 10 000 chats | < 30 ms query | `bench/cases/chat_list_10k.py` |
| Frontend bundle | < 200 KB gzip | `bench/cases/bundle_size.py` |
| Streaming at 500+ tok/s | no added backpressure | `bench/cases/stream_throughput.py` |
| Usable against a 1-2 tok/s host | no timeout, no stall | `bench/cases/slow_backend.py` |

These are CI gates, not aspirations (ADR-0015).

## Delivery phases

Each phase ends working, tested and benchmarked before the next begins.

1. ~~Skeleton, config, DB, migrations, auth, empty benchmark harness.~~ **Done.**
2. ~~Provider abstraction + full Ollama and llama.cpp adapters, streaming, persisted
   chat. **TTFT measured.**~~ **Done** — 10.2 ms p95 overhead, verified against fake
   backends and a real Ollama host.
3. ~~Minimal but real frontend: chat list, streaming, virtual scroll, markdown, tok/s.~~
   **Done** — verified in a real browser against a real Ollama host.
4. ~~Local model management in the UI (pull with progress, ps/unload, advanced params)
   and the parametrized OpenAI-compatible adapter with presets.~~ **Done** — downloads
   run as server-side jobs (ADR-0017), providers can be added from the interface with
   encrypted credentials, and conversations open one page at a time.
5. ~~Cloud providers: Groq, OpenRouter, Mistral, NVIDIA, Cloudflare, Gemini, Anthropic,
   OpenAI — each with contract tests.~~ **Done** — seven of the eight speak the OpenAI
   protocol and are presets over the phase-4 adapter; Anthropic is a second dedicated
   adapter ([ADR-0018](../adr/0018-anthropic-dedicated-adapter.md)).
6. ~~Organization, search, custom models, full multi-user.~~ **Done** — folders, tags,
   pin/archive, FTS5/tsvector search, custom model presets and an admin user console,
   all with keyset-paginated, dialect-neutral queries.
7. RAG.
8. MCP and tool calling.
9. Open WebUI import.
10. Optional plugins (images, voice), packaging, documentation.

## Decisions taken at review

All resolved on 2026-09-11:

1. **Web framework** — FastAPI, with msgspec on the hot path and Pydantic confined to
   cold routes (ADR-0001).
2. **ASGI server** — Granian by default, uvicorn+uvloop supported via
   `VELOX_SERVER=uvicorn` (ADR-0002).
3. **Frontend** — Svelte 5 (ADR-0012).
4. **License** — Apache-2.0.
5. **Database** — SQLite in WAL mode as the zero-config default, PostgreSQL optional
   (ADR-0003). The schema and repositories are dialect-neutral from phase 1 and asyncpg
   connectivity is supported there; the Postgres-specific search and vector
   implementations (tsvector, pgvector) land with phase 7.
