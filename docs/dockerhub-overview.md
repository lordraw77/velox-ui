# Docker Hub overview

This file is the source for the velox-ui repository description on Docker Hub.
Paste its contents (everything below the line) into the repository's **Overview**
field, or point an automated `docker/build-push-action` + `peter-evans/dockerhub-description`
step at it.

The image is published as [`lordraw/velox-ui`](https://hub.docker.com/r/lordraw/velox-ui)
by `.github/workflows/publish.yml`, which also pushes this description, so editing
this file and cutting a release is enough — there is nothing to paste by hand. CI
(`.github/workflows/ci.yml`) builds and size-checks on every push without publishing.

---

# velox-ui

A fast, self-hosted web interface for local and remote language models. Functional
parity with Open WebUI, built for latency and a small footprint.

**Local backends are the primary case, not a fallback.** No API key is required,
loading a model is reported as its own state rather than as a timeout, and a host
that is switched off degrades to an offline badge instead of an error.

```bash
docker run -d \
  --name velox-ui \
  -p 8080:8080 \
  -v velox-data:/data \
  lordraw/lordraw/velox-ui:latest
```

Open `http://localhost:8080` and create the first account — it becomes the
administrator. If Ollama is already running on the host, velox-ui finds it by
itself.

## What it does

- **Local backends first** — Ollama and llama.cpp have native adapters (model
  management, load state, the full sampling parameter set). LM Studio, vLLM, TGI,
  TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile, MLX LM and Text Generation WebUI
  work through an OpenAI-compatible adapter.
- **Cloud backends** — OpenAI, Groq, OpenRouter, Mistral, NVIDIA, Cloudflare
  Workers AI, Google Gemini and Anthropic. Credentials are encrypted at rest.
- **Streaming chat** with a branching message tree, virtual scrolling, markdown,
  and per-reply tokens/second. A reply belongs to the conversation, not to the
  page: open another chat, reload, or close the laptop and the model keeps
  going — coming back picks the reply up where it is, and one that finishes
  while you are elsewhere says so.
- **RAG** — knowledge collections, document upload, background ingest/embed, and
  citations streamed into the reply.
- **Tools** — MCP servers behind an approval gate, over stdio, Streamable HTTP or
  the legacy HTTP+SSE transport, with detection for a URL whose transport you do
  not know. Plus builtin tools a model can call mid-turn: the current date and
  time, and web search/browsing over a self-hosted SearXNG instance.
- **Optional plugins** — image generation and voice (transcription and speech)
  against any OpenAI-compatible endpoint. Disabled by default and never even
  imported when off.
- **Multi-user** — folders, tags, full-text search, custom model presets, an admin
  console, and API keys.
- **Import from Open WebUI** — `velox import openwebui` reconstructs the branching
  message tree, tags and folders from an Open WebUI chat export.
- **Works on a phone** — the interface is responsive, with a slide-over navigation
  drawer on small screens.

## Performance

These are CI gates, not marketing numbers — a regression fails the build.

| Target | Budget | Measured |
|---|---|---|
| Time-to-first-token overhead vs. calling the backend directly | < 15 ms p95 | ~10 ms |
| Container image | < 250 MB | ~189 MB |
| Idle memory | < 150 MB | ~131 MB |
| Open a 5 000-message conversation | < 150 ms | ~10 ms |
| Frontend bundle | < 200 KB gzip | ~50 KB |

No GPU, no Node.js and no PyTorch in the runtime image.

## Configuration

Everything has a working default; none of this is required.

| Variable | Default | Notes |
|---|---|---|
| `VELOX_DATA_DIR` | `/data` | Database, uploads, generated secret key. Mount it. |
| `VELOX_PORT` / `VELOX_HOST` | `8080` / `0.0.0.0` | |
| `VELOX_SECRET_KEY` | *(generated)* | Encrypts stored credentials. Generated into `/data` on first run — set it explicitly to survive a rebuilt volume. |
| `VELOX_DB_URL` | *(SQLite in `/data`)* | `postgresql+asyncpg://user:pass@host:5432/velox` for PostgreSQL. |
| `VELOX_AUTH_ENABLED` | `true` | `false` runs as a single local account with no login screen. |
| `VELOX_LOG_FORMAT` | `json` | `console` when a person is watching a terminal. |
| `VELOX_PROVIDER_OLLAMA_HOSTS` | *(autodiscovered)* | e.g. `http://192.168.1.10:11434`. |
| `VELOX_PROVIDER_<PRESET>_API_KEY` | — | e.g. `VELOX_PROVIDER_ANTHROPIC_API_KEY`. |

Backends, plugins and MCP servers can also be added from the interface, where
credentials are stored encrypted.

**On notifications**: a reply that finishes while you are in another conversation
or another tab always shows an in-app notice and a line in the tab title. The
desktop notification on top of that needs a secure context, which browsers grant
to HTTPS and to localhost but not to a plain `http://192.168.x.x` address — put
velox-ui behind a reverse proxy with TLS and it starts working, with nothing to
configure here.

## With Ollama

```yaml
services:
  velox-ui:
    image: lordraw/lordraw/velox-ui:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - velox-data:/data
    environment:
      VELOX_PROVIDER_OLLAMA_HOSTS: http://ollama:11434

  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    volumes:
      - ollama-models:/root/.ollama

volumes:
  velox-data:
  ollama-models:
```

## Image details

- Base: `python:3.12-slim`. Runs as a non-root `velox` user (uid 1000).
- Exposes `8080`; `VOLUME /data`.
- `HEALTHCHECK` probes `/ready` (database reachable and migrations current), not
  `/health` (process alive) — so an instance mid-migration is not sent traffic.
- Entrypoint is the `velox` CLI: `serve` by default, also `config check`,
  `migrate up`, `bench`, `import openwebui`, `import mcp-config`.

**Note on stdio MCP servers**: the runtime image deliberately ships no Node.js, so
MCP servers launched with `npx` cannot run inside this container. Run such a server
in [`lordraw/velox-ui-mcp-gateway`](https://hub.docker.com/r/lordraw/velox-ui-mcp-gateway)
instead — it republishes a stdio server as Streamable HTTP, which velox-ui then adds
as an ordinary `http_sse` server.

## Backups

Everything that must survive a container recreation is in the `/data` volume: the
SQLite database, uploaded and generated files, and `secret.key` if you did not set
`VELOX_SECRET_KEY`. Back it up as a unit — losing the key means re-entering every
stored credential. SQLite runs in WAL mode, so use
`sqlite3 velox.db ".backup backup.db"` rather than copying the file from under a
running process.

## Links

- Source, issues and documentation: https://github.com/lordraw77/velox-ui
- Configuration reference: [`docs/configuration.md`](https://github.com/lordraw77/velox-ui/blob/main/docs/configuration.md)
- Deployment guide: [`docs/deployment.md`](https://github.com/lordraw77/velox-ui/blob/main/docs/deployment.md)

Apache-2.0.
