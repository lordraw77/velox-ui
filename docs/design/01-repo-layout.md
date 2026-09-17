# Repository layout

Design document — not yet implemented. Reviewed before any code is written.

```
velox-ui/
├── pyproject.toml               # package velox-ui, module velox_ui, script `velox`
├── uv.lock
├── velox.example.toml           # fully commented reference config (English)
├── Dockerfile                   # multi-stage, distroless-ish final, target < 250 MB
├── docker-compose.yml           # velox-ui alone (SQLite, zero config)
├── docker-compose.ollama.yml    # velox-ui + ollama example stack
├── LICENSE                      # Apache-2.0
├── README.md                    # 3-line quickstart, provider table, benchmark table
├── CHANGELOG.md
├── CONTRIBUTING.md
│
├── src/velox_ui/
│   ├── __init__.py              # __version__ only; no side effects, no heavy imports
│   ├── __main__.py              # python -m velox_ui -> cli.main()
│   ├── cli.py                   # `velox serve|import|bench|migrate|config check|keys`
│   ├── app.py                   # ASGI app factory; route modules imported lazily
│   ├── lifespan.py              # startup/shutdown: engine, httpx pool, caches, jobs
│   ├── settings.py              # msgspec Structs; env (VELOX_*) + velox.toml merge
│   ├── logging.py               # structlog-style JSON logs, secret redaction filter
│   ├── errors.py                # typed error taxonomy + HTTP mapping
│   ├── metrics.py               # prometheus_client registry, histograms, per-model labels
│   ├── clock.py                 # monotonic helpers used by TTFT accounting
│   │
│   ├── api/
│   │   ├── deps.py              # auth principal, db session, rate limit guards
│   │   ├── sse.py               # byte-level SSE framing; passthrough + tee writer
│   │   ├── pagination.py        # keyset cursor encode/decode (opaque base64 msgpack)
│   │   ├── schemas/             # msgspec Structs for request/response bodies
│   │   └── routes/
│   │       ├── system.py        # /health /ready /metrics /api/version
│   │       ├── auth.py          # login, refresh, logout, register, oidc callback
│   │       ├── users.py admin.py
│   │       ├── apikeys.py
│   │       ├── providers.py     # CRUD + health + discovery + probe
│   │       ├── models.py        # unified model list, capabilities, visibility
│   │       ├── local_models.py  # ollama/llama.cpp management (pull/ps/unload/show)
│   │       ├── chats.py         # chat CRUD, tree ops, branching, search, export
│   │       ├── completions.py   # POST /api/chat/stream (the hot path)
│   │       ├── compare.py       # side-by-side multi-model runs
│   │       ├── folders.py tags.py prompts.py custom_models.py
│   │       ├── files.py rag.py
│   │       ├── mcp.py tools.py
│   │       ├── share.py         # public share links (unauthenticated read)
│   │       ├── openai_compat.py # /v1/chat/completions, /v1/models, /v1/embeddings
│   │       └── plugins.py
│   │
│   ├── services/                # all business logic; routes stay thin
│   │   ├── chat.py              # orchestrates a turn: context build -> provider -> tee
│   │   ├── context.py           # message tree -> provider messages, token budgeting
│   │   ├── branching.py         # edit/regenerate create siblings, active_leaf tracking
│   │   ├── persistence.py       # out-of-band writer queue (never blocks the stream)
│   │   ├── titles.py            # async auto-title generation
│   │   ├── models_registry.py   # discovery + LRU cache + manual refresh
│   │   ├── capabilities.py      # per-model capability resolution (probed, not hardcoded)
│   │   ├── routing.py           # fallback chains, visible provider switch events
│   │   ├── usage.py quota.py ratelimit.py
│   │   ├── search.py            # FTS5 / tsvector abstraction
│   │   ├── sharing.py export.py
│   │   └── importers/openwebui.py
│   │
│   ├── providers/
│   │   ├── base.py              # Protocol + shared Structs (the provider contract)
│   │   ├── registry.py          # type -> adapter factory; plugin entry points merged
│   │   ├── presets.toml         # vLLM, LM Studio, TGI, TabbyAPI, Kobold, Jan, ...
│   │   ├── presets.py           # preset loader/validator
│   │   ├── health.py            # cheap, cached, non-blocking health probes
│   │   ├── errors.py            # backend error -> typed ProviderError mapping helpers
│   │   ├── httpclient.py        # shared AsyncClient(s), HTTP/2, pools, keep-alive
│   │   ├── openai_compat/
│   │   │   ├── adapter.py       # parametrized OpenAI-compatible adapter
│   │   │   └── quirks.py        # per-preset deviations (usage chunk, stop, roles)
│   │   ├── ollama.py            # native /api/* adapter + model management
│   │   ├── llamacpp.py          # native /completion, /props, /slots, GBNF
│   │   ├── gemini.py anthropic.py mistral.py cloudflare.py
│   │   ├── openai.py groq.py openrouter.py nvidia.py   # thin presets over openai_compat
│   │   └── tools/
│   │       ├── translate.py     # OpenAI <-> Gemini <-> Anthropic tool schemas
│   │       ├── emulated.py      # prompt-based tool calling for local models
│   │       └── gbnf.py          # JSON-schema -> GBNF grammar
│   │
│   ├── rag/
│   │   ├── loaders/             # pdf, docx, md, txt, html, csv (entry-point pluggable)
│   │   ├── chunking.py
│   │   ├── embedders/           # fastembed_onnx.py, provider_backed.py
│   │   ├── store/               # sqlite_vec.py, pgvector.py
│   │   ├── retrieve.py          # hybrid BM25 + vector, RRF fusion
│   │   ├── rerank.py            # optional, ONNX cross-encoder
│   │   ├── citations.py
│   │   └── websearch/           # searxng.py, tavily.py, brave.py
│   │
│   ├── mcp/
│   │   ├── client.py stdio.py http_sse.py sse.py http_auto.py sse_events.py
│   │   ├── manager.py           # server lifecycle, tool cache, approval gate
│   │   └── schema_translate.py
│   │
│   ├── db/
│   │   ├── engine.py            # async engine, WAL pragmas, pool sizing
│   │   ├── models.py            # SQLAlchemy 2.0 typed ORM models
│   │   ├── types.py             # ULID, msgpack JSON column, UTC datetime
│   │   ├── repositories/        # chats.py messages.py users.py files.py ...
│   │   ├── fts/                 # sqlite_fts5.py, pg_tsvector.py + triggers
│   │   └── migrations/          # alembic env.py + versions/
│   │
│   ├── security/
│   │   ├── crypto.py            # AES-GCM secret box, key from VELOX_SECRET_KEY
│   │   ├── jwt.py apikeys.py password.py oidc.py
│   │   └── uploads.py           # type sniffing, size caps, path safety
│   │
│   ├── jobs/
│   │   ├── broker.py            # taskiq; in-process worker by default
│   │   └── tasks/ingest.py tasks/embed.py tasks/maintenance.py
│   │
│   ├── plugins/
│   │   ├── spec.py              # entry-point groups + Protocols
│   │   ├── loader.py            # discovery; disabled plugins are never imported
│   │   └── builtin/images/ stt/ tts/
│   │
│   └── web/                     # built frontend assets embedded in the wheel
│
├── frontend/
│   ├── package.json vite.config.ts tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.ts App.svelte
│       ├── lib/
│       │   ├── api/             # typed fetch client, SSE reader
│       │   ├── stream/          # incremental token sink, rAF-throttled flush
│       │   ├── virtual/         # virtual list (variable height, anchored)
│       │   ├── markdown/        # worker.ts: incremental md + highlight + KaTeX
│       │   ├── stores/          # runes-based state
│       │   └── i18n/            # en.json (source), it.json
│       └── routes/
│
├── bench/
│   ├── harness.py               # runs cases, prints a table, writes JSON
│   ├── fakes/                   # deterministic fake Ollama/llama.cpp/OpenAI servers
│   ├── cases/
│   │   ├── ttft_overhead.py     # direct-backend vs through-velox p50/p95/p99
│   │   ├── cold_start.py rss_idle.py image_size.py
│   │   ├── open_chat_5k.py      # end-to-end open of a 5k-message chat
│   │   ├── chat_list_10k.py     # keyset pagination query time
│   │   ├── stream_throughput.py # 500+ tok/s passthrough
│   │   └── slow_backend.py      # 1-2 tok/s UI responsiveness
│   └── README.md
│
├── tests/
│   ├── fakes/                   # shared fake backends (also used by bench)
│   ├── unit/ integration/ contract/ e2e/
│   └── conftest.py
│
└── docs/
    ├── design/                  # this set of documents
    ├── adr/
    ├── adding-a-provider.md
    ├── providers.md             # capability matrix
    ├── benchmarks.md            # methodology + results vs Open WebUI
    ├── configuration.md
    ├── deployment.md
    └── migration-openwebui.md
```

## Layering rule

`api/` may import `services/` and `api/schemas`. `services/` may import `providers/`,
`rag/`, `mcp/`, `db/`. Nothing below `services/` imports from `api/`. Route handlers
contain argument binding, authorization checks and response framing only.

## Import-cost rule (cold start < 1 s)

`velox_ui/__init__.py` and `cli.py` import nothing heavy. Optional subsystems
(`rag/`, `mcp/`, `jobs/`, `plugins/builtin/`) are imported at first use or at startup
only when enabled in config. Provider adapters are imported by the registry on demand.
A test asserts the import graph of a default startup stays under a module budget.
