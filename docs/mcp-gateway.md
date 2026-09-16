# The MCP gateway image

velox-ui speaks MCP over Streamable HTTP (ADR-0020) and over `stdio`. In a container,
only the first of those is usable: the runtime image carries no Node and no package
manager, so an MCP server published on npm or PyPI cannot be started from inside it —
there is no `npx`, no `uvx`. Configuring one as a `stdio` server fails with
`Could not start MCP server command 'npx': [Errno 2] No such file or directory`.

That is a deliberate consequence of two anti-requirements, not a gap: the frontend is
compiled in a build stage and copied in as static assets so the runtime has no Node
(`Dockerfile`), and ADR-0015 holds the image under 250 MB. Shipping Node, uv and git
inside velox-ui to support a feature most deployments never enable would cost every
user the weight of it.

`lordraw/velox-ui-mcp-gateway` is the other half. It runs a `stdio` MCP server and
republishes it as Streamable HTTP, so velox-ui reaches it as an ordinary `http_sse`
server over the network.

```
velox-ui ──HTTP POST /mcp──▶ gateway ──stdio──▶ MCP server
```

Source: `docker/mcp-gateway/Dockerfile`. Examples: `docker/mcp-gateway/examples/`.

## What is in the image

| | why |
|---|---|
| `supergateway` | the bridge itself: one `--stdio` command in, one Streamable HTTP endpoint out |
| Node + npm | npm-published MCP servers |
| `uv` | PyPI-published servers; uv fetches its own CPython, so there is no system Python to keep in sync |
| `git` | some servers pin a dependency to a git repository, and npm shells out to `git` to resolve it |

The base image is about 380 MB — Node, uv and git together. ADR-0015's 250 MB budget
is a promise about `lordraw/velox-ui` and does not extend here; keeping these
toolchains out of that image is the whole reason this one exists. Nothing is gained by
pulling it on a deployment that configures no MCP server, and nothing requires it.

It ships with **no MCP server installed**. Which servers you want is a deployment
decision, and baking a set into a public image makes everyone carry everyone else's.

## Adding servers

Build on top of the image, with the servers pinned:

```dockerfile
FROM lordraw/velox-ui-mcp-gateway:latest
USER root
RUN npm install -g --no-audit --no-fund discogs-mcp-server@0.5.7 \
 && uv tool install mcp-justwatch==0.0.1
USER node
```

`docker/mcp-gateway/examples/Dockerfile.derived` is that file, parameterised.
Building the repository's own `docker/mcp-gateway/Dockerfile` with
`--build-arg NPM_PACKAGES=` / `--build-arg UV_TOOLS=` does the same in one step.

Install at build time rather than letting `npx -y` or `uvx` fetch on every start: a
restart then needs no network and no writable package cache, and the version that ran
yesterday is the version that runs today. An MCP server is a tool surface a model
calls — a silent upgrade can rename a tool or change an argument under a conversation
that already learned the old shape.

## Running it

One `supergateway` process bridges exactly one `stdio` server. There is no
multiplexing of several servers onto one endpoint, so N servers means N containers.
They can share one image and all listen on 8000, since what distinguishes them is the
service name (on a compose network) or the published port (from outside).

```yaml
services:
  discogs-mcp:
    image: my-mcp-gateway:latest
    command:
      - --stdio
      - discogs-mcp-server
      - --outputTransport
      - streamableHttp
      - --stateful
      - --sessionTimeout
      - "600000"
      - --port
      - "8000"
    environment:
      DISCOGS_PERSONAL_ACCESS_TOKEN: ${DISCOGS_PERSONAL_ACCESS_TOKEN}
```

**Use `--stateful`.** A stateless gateway starts a fresh child process for every
request, so `tools/list` arrives at a server that never saw `initialize`, and a strict
implementation (FastMCP) answers `-32602 Invalid request parameters`. Stateful mode
keeps one child per session; velox-ui holds the `Mcp-Session-Id` the gateway returns
and sends it back on every later request (`mcp/http_sse.py`), so sessions work.

Then register each gateway in velox-ui as a server with transport `http_sse` and its
URL — no command, no arguments:

```
http://discogs-mcp:8000/mcp
```

The path matters: `supergateway` serves `/mcp` only (`--streamableHttpPath`), and a
`POST` to `/` is a 404. Tools from several servers merge — `GET /api/tools` is the
union over the caller's enabled servers (ADR-0020) — and a gateway that dies removes
only its own tools.

## Compose files in this repository

| file | what it runs |
|---|---|
| `docker-compose.mcp.yml` | velox-ui + one gateway (Discogs) as a sidecar |
| `docker-compose.mcp-multi.yml` | velox-ui + two gateways (Discogs, JustWatch) |
| `docker/mcp-gateway/examples/one-server.yml` | one gateway alone, for a velox-ui running elsewhere |
| `docker/mcp-gateway/examples/two-servers.yml` | two gateways alone |

The sidecar files publish no gateway port: the gateways stay on the compose network
where only velox-ui reaches them. The standalone examples must publish a port, and
bind it to `127.0.0.1` — **a gateway has no authentication of its own.** Anything that
can reach `/mcp` can call every tool the server behind it exposes, with whatever
credentials that server holds. Treat a published gateway port exactly like an
unauthenticated admin API.

Credentials go in a `.env` file next to the compose file (see
`docker/mcp-gateway/examples/.env.example`), which docker compose loads automatically
and `.gitignore` keeps out of the repository.

## Troubleshooting

| symptom in the velox-ui log | cause |
|---|---|
| `Could not start MCP server command 'npx'` | a `stdio` server configured inside velox-ui; use a gateway and `http_sse` instead |
| `MCP server returned HTTP 404` | the URL is missing `/mcp`, or has a stray character — velox-ui stores it verbatim |
| `The MCP server sent no response to the request` | the child process died before answering; `docker logs <gateway>` has its stderr, and a missing `git` is a common reason |
| `-32602 Invalid request parameters` on `tools/list` | the gateway is running stateless; add `--stateful` |

A working gateway answers a handshake directly:

```bash
docker exec -i velox-ui python - <<'PY'
import http.client, json
c = http.client.HTTPConnection("discogs-mcp", 8000, timeout=30)
c.request("POST", "/mcp",
          body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "probe", "version": "1"}}}),
          headers={"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"})
r = c.getresponse()
print(r.status, r.read().decode()[:300])
PY
```

Running it from inside the velox-ui container, rather than from the host, tests the
network path velox-ui actually uses.
