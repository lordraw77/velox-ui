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

Runs a `stdio` MCP server and republishes it as Streamable HTTP, so
[velox-ui](https://hub.docker.com/r/lordraw/velox-ui) — or any MCP client that speaks
HTTP — can reach it over the network.

```
client ──HTTP POST /mcp──▶ gateway ──stdio──▶ MCP server
```

The velox-ui runtime ships no Node.js and no package manager, by design: the frontend
is compiled in a build stage, and the image is held under 250 MB. An MCP server
published on npm or PyPI therefore cannot be launched from inside it. This image is
the other half of that decision.

## Quick start

The image ships **no MCP server**. Which ones you want is a deployment decision, so
add them on top:

```dockerfile
FROM lordraw/velox-ui-mcp-gateway:latest
USER root
RUN npm install -g --no-audit --no-fund @modelcontextprotocol/server-filesystem@2026.8.31
USER node
```

```bash
docker build -t my-mcp-gateway:latest .
docker run -d --name files-mcp -p 127.0.0.1:8811:8000 my-mcp-gateway:latest \
  --stdio "mcp-server-filesystem /data" \
  --outputTransport streamableHttp \
  --stateful --sessionTimeout 600000 \
  --port 8000
```

Then add it to velox-ui as a server with transport `http_sse` and the URL
`http://<address>:8811/mcp` — no command, no arguments.

## What is in it

| | why |
|---|---|
| `supergateway` | the bridge: one `--stdio` command in, one Streamable HTTP endpoint out |
| Node + npm | npm-published MCP servers |
| `uv` | PyPI-published servers; uv fetches its own CPython |
| `git` | some servers pin a dependency to a git repository, and npm shells out to `git` |

Install servers at build time rather than letting `npx -y` or `uvx` fetch them on
every start: a restart then needs no network and no writable cache, and the version
that ran yesterday is the version that runs today.

## Two things that will bite you

**Use `--stateful`.** Stateless mode starts a fresh child process per request, so
`tools/list` reaches a server that never saw `initialize`; a strict implementation
answers `-32602 Invalid request parameters`.

**A gateway has no authentication.** Anything that can reach `/mcp` can call every
tool the server behind it exposes, with whatever credentials that server holds. Keep
it on an internal network, or bind the published port to `127.0.0.1` and put a
proxy in front. Treat it like an unauthenticated admin API, because that is what it
is.

## Image details

- Base: `node:22-slim`, about 380 MB with the three toolchains. Runs as the non-root
  `node` user, working directory
  `/home/node` — a server may write next to itself at startup, and a read-only
  working directory kills it before the handshake.
- Exposes `8000`. Entrypoint is `supergateway`, so container arguments are its flags.
- Build args `NPM_PACKAGES` and `UV_TOOLS` (space-separated) install servers into a
  derived image.
- One process bridges one server: N servers means N containers, not one container
  with a list.

## Links

- Source, issues and documentation: https://github.com/lordraw77/velox-ui
- Gateway guide: [`docs/mcp-gateway.md`](https://github.com/lordraw77/velox-ui/blob/main/docs/mcp-gateway.md)
- Compose examples: [`docker/mcp-gateway/examples/`](https://github.com/lordraw77/velox-ui/tree/main/docker/mcp-gateway/examples)
