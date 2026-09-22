# Docker Hub overview — velox-ui-mcp-gateway

This file is the source for the velox-ui-mcp-gateway repository description on Docker
Hub. Everything below the line is pushed as the repository's **Overview** by
`.github/workflows/publish.yml`, the same way `docs/dockerhub-overview.md` is for
`lordraw/velox-ui`.

The image is published as
[`lordraw/velox-ui-mcp-gateway`](https://hub.docker.com/r/lordraw/velox-ui-mcp-gateway),
tagged from the same release as the application because the two ship from one commit
and are tested against each other. CI builds and smoke-tests it on every push
(`.github/workflows/ci.yml`) without publishing.

---

# velox-ui-mcp-gateway

Runs any `stdio` MCP server — you name it at run time — and republishes it as
Streamable HTTP, so [velox-ui](https://hub.docker.com/r/lordraw/velox-ui), or any MCP
client that speaks HTTP, can reach it over the network.

```
client ──HTTP POST /mcp──▶ gateway ──stdio──▶ MCP server
```

Every engine is already inside: `npx`, `uvx`, `python`, `bunx`, `deno`. Nothing to
build — the container's arguments are the server's command line, exactly as an MCP
client config spells it.

For `stdio` servers only. A server that already speaks HTTP needs no gateway: since
0.1.3 velox-ui connects to Streamable HTTP and to the older HTTP+SSE transport
directly, and detects which one a URL speaks.

## Quick start

```bash
docker run -d --name discogs-mcp \
  -p 127.0.0.1:8811:8000 \
  -v mcp-cache:/cache \
  -e DISCOGS_PERSONAL_ACCESS_TOKEN=... \
  lordraw/velox-ui-mcp-gateway:latest \
  npx -y discogs-mcp-server@0.5.7
```

Then add it to velox-ui as a server with transport `http_sse` and the URL
`http://<address>:8811/mcp` — no command, no arguments.

```yaml
services:
  discogs-mcp:
    image: lordraw/velox-ui-mcp-gateway:latest
    command: ["npx", "-y", "discogs-mcp-server@0.5.7"]
    environment:
      DISCOGS_PERSONAL_ACCESS_TOKEN: ${DISCOGS_PERSONAL_ACCESS_TOKEN}
    volumes:
      - mcp-cache:/cache

  justwatch-mcp:
    image: lordraw/velox-ui-mcp-gateway:latest
    command: ["uvx", "mcp-justwatch==0.0.1"]
    volumes:
      - mcp-cache:/cache

volumes:
  mcp-cache:
```

One container runs one server: N servers means N containers from the same image.

## Engines

| | for |
|---|---|
| `npx`, `node` | npm-published servers |
| `uvx`, `uv`, `python` | PyPI-published servers, and `python -m …`; CPython is preinstalled |
| `bunx`, `bun` | servers published for Bun |
| `deno` | JSR servers, or `deno run npm:…` |
| `git` | npm dependencies pinned to a git repository |

No `docker` CLI, on purpose: a server run as `docker run -i …` would need the host's
Docker socket, which is root on the host, handed to a process whose tool arguments a
model chooses.

## Three things that will bite you

**Pin the version in the command.** Packages are fetched at start, so
`npx -y some-server` is whatever is newest that day — and a new version can rename a
tool under a conversation that already learned the old one.

**Mount `/cache`.** npm, uv, Bun and Deno all cache there. A first start downloads (a
few seconds to a few tens of seconds); with the volume, a restart answers in about a
second. Without it, every recreation downloads again, and a start without network
fails.

**A gateway has no authentication.** Anything that can reach `/mcp` can call every
tool the server exposes, with whatever credentials it holds. Keep it on an internal
network, or bind the published port to `127.0.0.1`. Treat it like an unauthenticated
admin API, because that is what it is.

## Tuning

| variable | default | |
|---|---|---|
| `MCP_PORT` | `8000` | listening port |
| `MCP_STATEFUL` | `true` | one server process per session — leave it on: stateless mode sends `tools/list` to a process that never saw `initialize` |
| `MCP_SESSION_TIMEOUT_MS` | `600000` | idle session lifetime |
| `SUPERGATEWAY_ARGS` | — | extra supergateway flags |

Arguments starting with `--` go to supergateway untouched, so a `command` of
`[--stdio, …]` still works.

## Image details

- Base `node:22-slim`, about 640 MB with every engine. Runs as the non-root `node`
  user.
- Exposes `8000`; the MCP endpoint is `/mcp`.
- `HEALTHCHECK` on `/healthz` — liveness of the gateway. The server behind it starts
  per session, so a failed package download shows in `docker logs`, not in the
  health status.
- No arguments: prints usage and exits 64.

## Links

- Source, issues and documentation: https://github.com/lordraw77/velox-ui
- Gateway guide: [`docs/mcp-gateway.md`](https://github.com/lordraw77/velox-ui/blob/main/docs/mcp-gateway.md)
- Compose examples: [`docker/mcp-gateway/examples/`](https://github.com/lordraw77/velox-ui/tree/main/docker/mcp-gateway/examples)
