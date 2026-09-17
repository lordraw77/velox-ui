# The MCP gateway image

velox-ui speaks MCP over Streamable HTTP (ADR-0020) and over `stdio`. In a container,
only the first of those is usable: the runtime image carries no Node and no package
manager, so an MCP server published on npm or PyPI cannot be started from inside it —
there is no `npx`, no `uvx`. Configuring one as a `stdio` server fails with
`Could not start MCP server command 'npx': [Errno 2] No such file or directory`.

That is a deliberate consequence of two anti-requirements, not a gap: the frontend is
compiled in a build stage and copied in as static assets so the runtime has no Node
(`Dockerfile`), and ADR-0015 holds the image under 250 MB. Shipping every MCP engine
inside velox-ui, for a feature most deployments never enable, would cost every user
the weight of it.

`lordraw/velox-ui-mcp-gateway` is the other half. It carries every engine, runs the
MCP server you name, and republishes it as Streamable HTTP, so velox-ui reaches it as
an ordinary `http_sse` server over the network.

```
velox-ui ──HTTP POST /mcp──▶ gateway ──stdio──▶ MCP server
```

Source: `docker/mcp-gateway/`. Examples: `docker/mcp-gateway/examples/`.

## Naming the server

The container's arguments are the server's own command line — the same `command` and
`args` an MCP client config already has. There is nothing to build.

```bash
docker run -p 127.0.0.1:8811:8000 lordraw/velox-ui-mcp-gateway npx -y discogs-mcp-server@0.5.7
docker run -p 127.0.0.1:8812:8000 lordraw/velox-ui-mcp-gateway uvx mcp-justwatch==0.0.1
```

A desktop config translates one to one:

```json
"discogs": { "command": "npx", "args": ["-y", "discogs-mcp-server@0.5.7"],
             "env": { "DISCOGS_PERSONAL_ACCESS_TOKEN": "..." } }
```

```yaml
discogs-mcp:
  image: lordraw/velox-ui-mcp-gateway:latest
  command: ["npx", "-y", "discogs-mcp-server@0.5.7"]
  environment:
    DISCOGS_PERSONAL_ACCESS_TOKEN: ${DISCOGS_PERSONAL_ACCESS_TOKEN}
  volumes:
    - mcp-cache:/cache
```

Arguments reach the server exactly as given — spaces, quotes and `$` included. The
entrypoint quotes each one for the shell supergateway starts the server through.

With no arguments the container prints what it expects and exits with status 64.

## What is in the image

| engine | for |
|---|---|
| `npx`, `node` | npm-published servers, by far the most common |
| `uvx`, `uv` | PyPI-published servers |
| `python` | servers started as `python -m …`; a uv-managed CPython, preinstalled so the first `uvx` does not download one |
| `bunx`, `bun` | servers published for Bun |
| `deno` | servers on JSR, or run with `deno run npm:…` |
| `git` | not optional: some servers pin a dependency to a git repository, and npm shells out to `git` to resolve it |

It ships no MCP server. And it deliberately has no `docker` CLI: a server launched as
`docker run -i …` would need the host's `/var/run/docker.sock`, which is root on the
host with no mediation, given to a process whose tool arguments a model chooses. When
a server is only documented as a Docker image, its Dockerfile nearly always ends in an
`npx`, `uvx` or `node` command this image can run directly.

The image is about 640 MB with every engine. ADR-0015's 250 MB budget is a promise
about `lordraw/velox-ui` and does not extend here; keeping these toolchains out of that
image is the reason this one exists.

## Fetched at start: pinning and the cache

Nothing is installed ahead of time, so plan for two things.

**Pin every version in the command.** `npx -y discogs-mcp-server` is whatever version
is newest the day the container starts. An MCP server is a tool surface a model calls —
a silent upgrade can rename a tool or change an argument under conversations that
already learned the old shape. `discogs-mcp-server@0.5.7`, `mcp-justwatch==0.0.1`.

**Mount a volume at `/cache`.** npm, uv, Bun and Deno all keep their caches under it.
Measured with the two servers above: the first start of each took 13 s (npx) and 6 s
(uvx) to answer `tools/list`; after a restart with the cache in place, 1.3 s each.
Gateways can share one cache volume — every one of those package managers tolerates
concurrent use of its cache. Without the volume, every container recreation downloads
again, and a start with no network fails.

## Running it

One gateway container runs exactly one server. There is no multiplexing of several
servers onto one endpoint, so N servers means N containers. They share the image and
the cache volume and all listen on 8000; what distinguishes them is the service name
(on a compose network) or the published port (from outside).

Register each in velox-ui as a server with transport `http_sse` and its URL — no
command, no arguments:

```
http://discogs-mcp:8000/mcp
```

The path matters: the gateway serves `/mcp` only, and a `POST` to `/` is a 404. Tools
from several servers merge — `GET /api/tools` is the union over the caller's enabled
servers (ADR-0020) — and a gateway that dies removes only its own tools.

### Tuning

All optional, as environment variables:

| variable | default | |
|---|---|---|
| `MCP_PORT` | `8000` | port the gateway listens on |
| `MCP_STATEFUL` | `true` | one server process per session |
| `MCP_SESSION_TIMEOUT_MS` | `600000` | idle session lifetime |
| `SUPERGATEWAY_ARGS` | — | extra supergateway flags, space-separated |

**Leave `MCP_STATEFUL` on.** A stateless gateway starts a fresh server process for
every request, so `tools/list` arrives at a server that never saw `initialize`, and a
strict implementation (FastMCP) answers `-32602 Invalid request parameters`. velox-ui
holds the `Mcp-Session-Id` the gateway returns and sends it back on every later
request (`mcp/http_sse.py`), so sessions cost nothing.

Arguments that start with `--` are handed to supergateway untouched, so compose files
written for the first version of this image — `command: [--stdio, …]` — keep working.

### Health

The image has a `HEALTHCHECK` against `/healthz`. It is liveness: *healthy* means the
gateway is answering. The server behind it starts per session, so a package that fails
to download does not make the container unhealthy — the first connect from velox-ui is
what proves that, and `docker logs` has the server's stderr.

## Compose files in this repository

| file | what it runs |
|---|---|
| `docker-compose.mcp.yml` | velox-ui + one gateway (Discogs, via `npx`) as a sidecar |
| `docker-compose.mcp-multi.yml` | velox-ui + two gateways (Discogs via `npx`, JustWatch via `uvx`) + SearXNG |
| `docker/mcp-gateway/examples/one-server.yml` | one gateway alone, for a velox-ui running elsewhere |
| `docker/mcp-gateway/examples/two-servers.yml` | gateways alone, with commented `bunx` and `deno` services |

The sidecar files publish no gateway port: the gateways stay on the compose network
where only velox-ui reaches them. The standalone examples must publish a port, and
bind it to `127.0.0.1` — **a gateway has no authentication of its own.** Anything that
can reach `/mcp` can call every tool the server behind it exposes, with whatever
credentials that server holds. Treat a published gateway port exactly like an
unauthenticated admin API.

SearXNG is in the multi file but not behind a gateway: velox-ui talks to it directly
through its builtin web-search plugin (`docs/configuration.md`).

Credentials go in a `.env` file next to the compose file (see
`docker/mcp-gateway/examples/.env.example`), which docker compose loads automatically
and `.gitignore` keeps out of the repository.

## Troubleshooting

| symptom in the velox-ui log | cause |
|---|---|
| `Could not start MCP server command 'npx'` | a `stdio` server configured inside velox-ui; use a gateway and `http_sse` instead |
| `MCP server returned HTTP 404` | the URL is missing `/mcp`, or has a stray character — velox-ui stores it verbatim |
| `The MCP server sent no response to the request` | the server process died before answering: a typo in the package name, no network on a first start, or a dependency npm could not resolve. `docker logs <gateway>` shows its stderr |
| `-32602 Invalid request parameters` on `tools/list` | the gateway is running stateless; unset `MCP_STATEFUL=false` |

A working gateway answers a handshake directly:

```bash
docker exec -i velox-ui python - <<'PY'
import http.client, json
c = http.client.HTTPConnection("discogs-mcp", 8000, timeout=60)
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
network path velox-ui actually uses. On a first start the answer can take as long as
the package download.
