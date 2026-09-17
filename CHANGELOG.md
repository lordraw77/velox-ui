# Changelog

All notable changes to velox-ui are recorded here. Entries state only what has
actually shipped (docs/design/00-overview.md's own rule); nothing here is
aspirational.

## [Unreleased]

### Added

- MCP servers over the legacy HTTP+SSE transport (`sse`), and transport
  detection (`http_auto`), alongside Streamable HTTP (`http_sse`, unchanged).
  Servers built on the Python MCP SDK's `/sse` route could not be added before.
  Detection follows the MCP specification's backwards-compatibility procedure,
  plus the signal that SDK actually sends: 200 with an `endpoint` event rather
  than a 4xx. The legacy client refuses a message endpoint announced on another
  origin, so a server cannot redirect requests — and their credentials — to a
  different host. Imported configs follow their declared `type`; one without a
  type is detected. See ADR-0022.

### Fixed

- Adding a legacy SSE server as Streamable HTTP hung indefinitely instead of
  failing. The client read each response to its end with a per-read timeout,
  and the server's keep-alive pings reset it every 15 seconds. Both HTTP
  clients now read event streams incrementally, stop at their answer, and
  enforce one overall deadline per request; the Streamable HTTP client names a
  legacy endpoint as such when it reaches one.
- Every MCP connect and tool call left a server session open until its idle
  timeout, because velox-ui never ended the sessions it opened. Behind the MCP
  gateway each session is a server process, so each tool call left one running
  for ten minutes. The Streamable HTTP client now sends `DELETE` for its
  session on close.

## [0.1.2] - 2026-09-17

The MCP gateway runs any server named at run time, with every engine inside,
and SearXNG joins the multi-server deployment for the builtin web search.

### Changed

- `lordraw/velox-ui-mcp-gateway` now carries every MCP engine — `npx`,
  `uvx`, `python`, `bunx`, `deno` — and runs whichever server the container's
  arguments name: `command: ["npx", "-y", "discogs-mcp-server@0.5.7"]`, the
  same `command` and `args` an MCP client config already has. Adding or
  changing a server no longer means a build; the `NPM_PACKAGES` and
  `UV_TOOLS` build arguments are gone. Packages are fetched when the container
  starts, so pin versions in the command and mount a volume at `/cache`:
  measured with Discogs and JustWatch, a first start answered `tools/list` in
  13 s and 6 s, a restart with the cache in 1.3 s. Gateways run stateful by
  default and gain a `HEALTHCHECK`. Arguments starting with `--` still go to
  supergateway untouched, but servers are no longer preinstalled, so a 0.1.1
  `command: [--stdio, discogs-mcp-server, …]` must name the package through an
  engine (`npx -y discogs-mcp-server@0.5.7`). The image grows to about 640 MB
  (from about 380), almost all of it Bun, Deno and the preinstalled CPython.

### Added

- A SearXNG service in `docker-compose.mcp-multi.yml`, with
  `docker/searxng/settings.yml`, for the builtin `web_search` and `web_browse`
  tools. It sits beside the MCP gateways rather than behind one: velox-ui calls
  SearXNG directly, so an MCP wrapper would only put a second, identical search
  tool in front of the model. The settings file enables the `json` format, whose
  absence makes the official image answer the plugin with 403, and leaves the
  secret to `SEARXNG_SECRET`, without which the container exits at startup.

## [0.1.1] - 2026-09-16

Publishing: both images now reach Docker Hub from a tag, and the pipeline that
gates them runs again.

### Added

- `lordraw/velox-ui-mcp-gateway`, a companion image that runs a `stdio` MCP
  server and republishes it as Streamable HTTP. The runtime image ships no
  Node and no package manager (the frontend is compiled in a build stage, and
  ADR-0015 holds the image under 250 MB), so an MCP server published on npm or
  PyPI cannot be launched from inside it — configuring one as a `stdio` server
  failed with `No such file or directory: 'npx'`. The gateway is the other
  half of that decision: velox-ui reaches the server as an ordinary `http_sse`
  server over the network. Source in `docker/mcp-gateway/`, compose examples
  alongside it, and `docs/mcp-gateway.md` for the whole arrangement. The image
  ships with no MCP server installed; servers are added in a derived build,
  pinned.
- A release workflow (`.github/workflows/publish.yml`) that pushes both
  images to Docker Hub on a `v*` tag, or on demand for a one-off tag, and
  pushes each repository's description from `docs/dockerhub-overview*.md` in
  the same run. The 250 MB budget (ADR-0015) is checked against the loaded
  image before the push, so a release cannot exceed it quietly. Until now CI
  built and gated the image but nothing published it.
- `docker-compose.mcp.yml` and `docker-compose.mcp-multi.yml`, deployment
  variants running velox-ui with one and with two MCP servers behind gateway
  sidecars. One `supergateway` process bridges exactly one server, so N
  servers means N containers; their tools merge in velox-ui, which unions the
  caller's enabled servers (ADR-0020).

### Changed

- The Ollama host in every compose file is now `${OLLAMA_HOSTS}`, read from
  `.env` like the provider API keys, instead of an address baked into the
  committed file. Unset, it resolves to an empty value, which leaves the
  first-start autodiscovery in place rather than registering a dead host.
- `VELOX_BENCH_HEADROOM_<CASE>` grants one benchmark case a stated allowance
  over its target, for the gap between the machine a target was calibrated on
  and the machine a build runs on. A case that needs it reports `tolerated`
  rather than `pass` and is listed separately, the table keeps showing the
  declared target, and there is no global switch — so the slack stays visible
  instead of disappearing into a green build. CI grants `rss_idle` 25 MB:
  ~131 MB here, ~150.5 MB on a GitHub runner, against a 150 MB target.

### Fixed

- Every Python job in CI had failed since the runner image moved to a PEP 668
  "externally managed" interpreter: `UV_SYSTEM_PYTHON=1` sent
  `uv pip install -e .` at `/usr`, where uv refuses. Each job now builds a
  virtualenv with `uv venv` and puts it on `PATH`. Two failures behind that
  one surfaced and are fixed too: mypy could not resolve the stub-less
  `fastembed` import, and the benchmark job installed no `uvicorn`, so
  `open_chat_5k` and `ttft_overhead` reported themselves as skipped — a gate
  that passed by not running.

## [0.1.0] - 2026-09-16

The first release: all ten delivery phases of docs/design/00-overview.md, from
the skeleton to the optional plugins.

### Added

- A builtin `current_datetime` tool, so a model can check the actual date and
  time instead of answering from its training cutoff. It needs no
  configuration, which made the `velox_ui.tools` entry-point group properly
  plural: every registered plugin now contributes whatever tools its config
  supports, so enabling the group with no SearXNG address still gives you the
  date and time, and the websearch plugin offers nothing until it has one.
- Web search and browsing as model-invokable tools: a builtin `"tools"`
  plugin (ADR-0014's dormant `velox_ui.tools` entry-point group, first used
  here) over a self-hosted SearXNG instance, offering `web_search` and
  `web_browse` with no approval gate. Wired into the real tool-calling loop
  in `services/chat.py`, not just a UI button — a custom model's "Web search
  & browsing" checkbox (`CustomModel.plugins`, finally consumed by the
  frontend) resolves into `web_tools: true` on a completion request the same
  way MCP's `tool_server_ids` already works. `web_browse` is SSRF-guarded:
  every hostname and every redirect hop is checked against
  loopback/private/link-local/reserved/multicast ranges before being
  fetched. `GET /api/tools` and `POST /api/websearch` (a phase-7 stub) both
  now reflect this plugin when enabled. Web tools are offered on every chat
  once the plugin is enabled (the same global-toggle shape images/voice use),
  not only from a custom model that opted in.
- Responsive support for phones and tablets.
- Phase 10 — optional plugins, packaging, documentation. Image generation and
  voice (STT/TTS) ship as builtin entry-point plugins (ADR-0014) that are thin
  OpenAI-compatible HTTP clients (ADR-0021), disabled by default and never
  imported when off. `GET/PUT /api/plugins/{name}`, `POST
  /api/images/generate`, `POST /api/audio/transcribe`, `POST
  /api/audio/speech`. A settings page, a mic button, a speaker button and an
  image-generation panel in the interface, all hidden until their plugin is
  enabled. `CHANGELOG.md`, `CONTRIBUTING.md` and the remaining reference docs.
- `velox import mcp-config` and `POST /api/mcp/servers/import` (plus an
  "Import from JSON" box in the MCP settings panel) translate a Claude Code
  `mcpServers` config into velox-ui's own MCP server shape
  (`services/mcp_import.py`). The manual "add a server" form also gained a
  credential env-var-name field (`config.auth_env`), previously only settable
  through the API.
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

### Fixed

- The Ollama adapter never sent tools to the model. It reported
  `ToolSupport.NATIVE` from `/api/show`, so the service layer believed tools
  were offered, but `_encode_request` never put `tools` in the `/api/chat`
  body — a tool-capable model was silently given none and answered "I cannot
  browse the web". Tool results and assistant tool-call turns were not
  encoded either. Ollama's response shape also differs from OpenAI's (a call
  arrives whole rather than fragmented, arguments are an object not a string,
  no call id, and `done_reason: "stop"` even for a tool-only turn), all now
  handled. Missed until now because every tool test drove the loop through
  the `openai_compat` adapter; the native Ollama path now has contract tests
  and its own end-to-end tool-loop test.
