# Deployment

## Docker (recommended)

```
docker compose up -d
```

`docker-compose.yml` runs velox-ui alone, SQLite in a named volume, port
`8080`. Open the address, create the first account — it becomes the
administrator — and go. If Ollama is already running on the Docker host,
velox-ui finds it by itself (autodiscovery probes loopback only, so a host
service is reached through `host.docker.internal` — see the commented
`extra_hosts` entry in the compose file for platforms where Docker doesn't
add that automatically).

`docker-compose.ollama.yml` runs velox-ui and Ollama together, with a
commented block to run Ollama with GPU acceleration. Everything works
without a GPU, only slower (ADR-0008) — CPU-only is a supported
configuration, not a degraded one.

Both files set `image: velox-ui:latest` with `build: .` alongside it;
comment out `build` to pull a published image instead of building locally,
or the reverse to always build from the source checkout.

### Image

Three-stage build (`Dockerfile`): the frontend is compiled with Node and
copied into the Python package as static assets, so the runtime image
carries no Node at all; dependencies are installed into a self-contained
virtualenv in a builder stage; the runtime stage copies only that venv onto
`python:3.12-slim`, as a non-root user. No torch, no CUDA, ever — embeddings
run on ONNX Runtime and download on first use into the data volume rather
than the image (ADR-0010). CI enforces the resulting image stays under 250
MB (ADR-0015; current: ~189 MB, see `docs/benchmarks.md`).

### Persisted data

Everything that must survive a container recreation lives under the `/data`
volume: the SQLite database (or nothing, for PostgreSQL), uploaded and
generated files, and `secret.key` if `VELOX_SECRET_KEY` was left unset. Back
up the volume as a unit — losing `secret.key` without the database it
encrypts credentials for means re-entering every provider and plugin
credential.

## PostgreSQL instead of SQLite

Install the `postgres` extra (already in the container image) and point
`db.url` (or `VELOX_DB_URL`) at a server:

```
VELOX_DB_URL=postgresql+asyncpg://velox:password@db:5432/velox
```

The schema and repositories are dialect-neutral from phase 1 (ADR-0003);
full-text search and vector retrieval use PostgreSQL-native `tsvector` and
`pgvector` in place of SQLite's FTS5 and sqlite-vec, transparently.

## Reverse proxy

The streaming completion endpoint (`POST /api/chat/stream`) is
Server-Sent Events over a long-lived connection. A proxy in front of
velox-ui needs:

- Buffering disabled for the streaming path (nginx: `proxy_buffering off;`
  on the location; Caddy and Traefik don't buffer SSE by default).
- A read timeout longer than the slowest model response you expect —
  velox-ui itself applies no timeout to a backend that is still generating
  (ADR-0008: a slow local model is a normal state, not a failure).
- WebSocket-style upgrade headers are **not** needed; this is plain HTTP
  chunked streaming, not a WebSocket.

`GET /health` is a liveness probe (process is up); `GET /ready` additionally
checks the database is reachable and migrations are current — use `/ready`
for a load balancer's health check and the container's own `HEALTHCHECK`
(which already does).

## Bare metal / without Docker

```
uv sync --extra postgres --extra uvicorn   # or pip install 'velox-ui[postgres,uvicorn]'
cd frontend && npm ci && npm run build     # writes into src/velox_ui/web/
velox migrate up
velox serve
```

`server = "granian"` (default) or `"uvicorn"` (`VELOX_SERVER=uvicorn`) in
`velox.toml`. `workers` stays at `1` deliberately (ADR-0002) — scale by
running multiple instances behind a load balancer against a shared
PostgreSQL database instead, if you need more than one process.

## Backup

SQLite runs in WAL mode: a plain file copy of `velox.db` while the process
is running can capture a torn write. Use `sqlite3 velox.db ".backup
backup.db"`, or stop the container briefly, or switch to PostgreSQL and use
its own backup tooling if continuous operation during backup matters.
