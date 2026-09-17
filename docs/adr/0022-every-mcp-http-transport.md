# ADR-0022: Every MCP HTTP transport — Streamable HTTP, legacy HTTP+SSE, and detection

**Status:** accepted (supersedes the transport section of ADR-0020)
**Date:** 2026-09-17

## Context
ADR-0020 implemented one HTTP transport, Streamable HTTP, on the reasoning that the
2025-03-26 revision of the MCP specification had superseded HTTP+SSE and "every
current MCP server implements" the new one. The second half did not hold. Servers
built on the Python MCP SDK's `/sse` route are common and still deployed; the first
one a user tried to add (`linux-ssh-mcp`, `http://…:9980/sse`) could not be configured
at all.

Running the existing client against that server showed the failure was worse than an
unsupported transport. `HttpSseMcpClient` read each response body to its end, with
httpx's 30-second timeout applying *per read*. The legacy server answers a `POST /sse`
with 200 and an event stream that never closes, and sends `: ping` every 15 seconds —
so every read succeeded, the timeout never fired, and `initialize` never returned. The
interface waited indefinitely with no error. The same flaw would hang a Streamable
HTTP server that keeps its response stream open, which the specification allows.

A third problem surfaced in the same investigation. velox-ui opens a connection per
operation (ADR-0020) and never ended the sessions it opened, so a stateful Streamable
HTTP server kept one session alive per connect and per tool call until its idle
timeout. The MCP gateway image runs stateful, and there each session is a server
process: every tool call left a process running for ten minutes.

## Decision

### Three HTTP transports, stored values unchanged
`mcp_server.transport` accepts `stdio`, `http_sse`, `sse` and `http_auto`. `http_sse`
keeps meaning Streamable HTTP — the name is historical and misleading, but every
server stored under ADR-0020 has it, and renaming stored values would need a data
migration and a compatibility window for the API for no functional gain. The interface
shows labels instead ("Streamable HTTP", "SSE (legacy HTTP+SSE)", "HTTP — detect"). The
column is `String(16)` with no constraint, so no migration is needed.

### Streams are read incrementally, against an overall deadline
`mcp/sse_events.py` parses server-sent events as they arrive. Both HTTP clients use it
and stop reading at the event that answers their request, skipping notifications,
server-to-client requests, other responses and comments. Neither ever reads a stream to
its end. Each request has one deadline for the whole exchange (60 s, as stdio), enforced
with `asyncio.timeout`; the default HTTP client has no read timeout, because a per-read
timeout is exactly what keep-alive pings defeat.

A Streamable HTTP request that receives an `endpoint` event first raises
`McpLegacySseEndpointError`, which names the problem and the transport to use instead.

### Sessions are ended on close
`HttpSseMcpClient.close` sends `DELETE` with the `Mcp-Session-Id`, as the specification
asks of clients that no longer need a session, best-effort under a five-second limit.
Against the gateway this is observable: the server process exits with SIGTERM as the
client closes.

### The legacy transport: `mcp/sse.py`
`GET` opens the stream; the first `endpoint` event names where to `POST` messages; each
response arrives on the stream, matched to its request by JSON-RPC id through pending
futures — the arrangement `mcp/stdio.py` already uses for a subprocess. A response in
the `POST` body is accepted too. If the stream ends, every pending request fails at
once rather than waiting out its deadline.

The announced endpoint is validated, not trusted: it must share the stream URL's scheme,
host and port. Otherwise a server could name another host, and every message — with any
`Authorization` header configured for this server — would go there. A refused endpoint
fails the connection before any message is sent.

### Detection: `mcp/http_auto.py`
`http_auto` follows the specification's backwards-compatibility procedure: `initialize`
as Streamable HTTP, and on 400, 404 or 405, the legacy transport on the same URL. One
signal is added from observation rather than from the specification: 200 with an
`endpoint` event first, which is what the Python SDK's `/sse` route answers. Nothing
else switches transports — a timeout, a refused connection or a 401 is a failure of
this server, reported as one, not retried as the other transport. When both fail, the
error names both failures.

### Imports follow the declared type
`velox import mcp-config` and the import endpoint map `"type": "http"` (and the
`streamable-http` spellings) to `http_sse`, `"sse"` to `sse`, and an entry with no type
to `http_auto`. Before this, every URL became `http_sse`, so an imported legacy server
could never connect.

## Consequences
- velox-ui now maintains a transport the specification has deprecated. It is about two
  hundred lines, isolated in one module, and tested against a fake on a real socket;
  removing it later is deleting that module and one entry in `build_client`.
- The fakes for both HTTP transports run on localhost sockets
  (`tests/fakes/mcp_http.py` with `run_fake`): `httpx.ASGITransport` buffers a response
  until its body ends, so a stream that stays open cannot be tested through it.
- `http_auto` costs one extra round trip against a legacy server, and a URL that speaks
  neither transport is tried twice before failing. Choosing the transport explicitly
  avoids both.
- A misconfigured URL now fails within the request deadline with a message that names
  the URL and the cause, instead of hanging a connect or a chat turn.
- The `http_sse` value remains a trap for anyone reading the database or the API
  directly. The interface, the API docstrings and this record say what it means.
- Bridging a legacy server through the MCP gateway (`mcp-remote`, `docs/mcp-gateway.md`)
  still works, but is no longer needed for velox-ui itself.
