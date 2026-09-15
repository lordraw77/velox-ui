# Changelog

All notable changes to velox-ui are recorded here. Entries state only what has
actually shipped (docs/design/00-overview.md's own rule); nothing here is
aspirational.

## [Unreleased]

### Added

- Phase 10 — optional plugins, packaging, documentation. Image generation and
  voice (STT/TTS) ship as builtin entry-point plugins (ADR-0014) that are thin
  OpenAI-compatible HTTP clients (ADR-0021), disabled by default and never
  imported when off. `GET/PUT /api/plugins/{name}`, `POST
  /api/images/generate`, `POST /api/audio/transcribe`, `POST
  /api/audio/speech`. A settings page, a mic button, a speaker button and an
  image-generation panel in the interface, all hidden until their plugin is
  enabled. `CHANGELOG.md`, `CONTRIBUTING.md` and the remaining reference docs.
- Phase 9 — Open WebUI import. `velox import openwebui` reads Open WebUI's own
  chat export (not its database file), reconstructing the branching message
  tree, tags and, given a folder export, folder names. Idempotent by the
  source chat id.
- Phase 8 — MCP and tool calling. stdio and Streamable HTTP MCP clients, a
  server manager with a persisted tool cache and an approval gate
  (ADR-0020), native tool calling on the OpenAI-compatible and Anthropic
  adapters, prompt-based emulation for models without native support, and a
  bounded in-stream tool-call loop. `custom_model.tools` selects enabled MCP
  servers for a chat.
- Phase 7 — RAG. Knowledge-base collections, document upload and background
  ingest/embed jobs (ADR-0019), local ONNX (fastembed) or provider-backed
  embedders, sqlite-vec/pgvector retrieval, a debug/preview query endpoint,
  and citations streamed into chats whose custom model carries
  `knowledge_ids`.
- Phase 6 — organization, search, custom models, full multi-user. Folders,
  tags, pin/archive, FTS5/tsvector search, custom model presets and an admin
  user console, all with keyset-paginated, dialect-neutral queries.
- Phase 5 — cloud providers: Groq, OpenRouter, Mistral, NVIDIA, Cloudflare,
  Gemini, Anthropic, OpenAI. Seven speak the OpenAI protocol as presets over
  the phase-4 adapter; Anthropic is a dedicated adapter (ADR-0018).
- Phase 4 — local model management in the interface (pull with progress,
  ps/unload, advanced params) and the parametrized OpenAI-compatible adapter
  with presets. Downloads run as server-side jobs (ADR-0017); providers can
  be added from the interface with encrypted credentials; conversations open
  one page at a time.
- Phase 3 — the web interface: chat list, streaming, virtual scroll,
  markdown, tokens/second.
- Phase 2 — provider abstraction, full Ollama and llama.cpp adapters,
  streaming, persisted chat. 10.2 ms p95 TTFT overhead, verified against fake
  backends and a real Ollama host.
- Phase 1 — skeleton, configuration, database, migrations, authentication,
  the benchmark harness.
