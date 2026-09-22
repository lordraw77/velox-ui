# HTTP API

Design document. All paths are prefixed `/api` except the OpenAI-compatible surface
(`/v1`), system endpoints and static assets. Request/response bodies are JSON encoded
with msgspec. Authentication is a bearer JWT or an API key (`Authorization: Bearer …`);
public share routes are unauthenticated by design.

Legend: **A** = admin only, **P** = public (no auth), **S** = streaming, **○** =
designed here but not built yet. The marks are checked against the running
application's OpenAPI schema, so a row without **○** is a route that exists.

## System

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | **P** liveness; never touches the DB or any provider |
| GET | `/ready` | **P** DB reachable + migrations current |
| GET | `/metrics` | Prometheus exposition (optionally **A**-gated) |
| GET | `/api/version` | **P** version, build, enabled features |
| GET | `/api/config` | Client bootstrap: features, auth mode, locale list |

## Auth and users

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | **P** when open registration is enabled |
| POST | `/api/auth/login` | returns access JWT + refresh cookie |
| POST | `/api/auth/refresh` | rotating refresh tokens, reuse detection |
| POST | `/api/auth/logout` | revokes the refresh family |
| GET | `/api/auth/oidc/login` | ○ **P** optional OIDC start |
| GET | `/api/auth/oidc/callback` | ○ **P** |
| GET/PATCH | `/api/auth/me` | profile + UI settings |
| GET/POST | `/api/me/api-keys` | key is returned once, on creation |
| DELETE | `/api/me/api-keys/{id}` | |
| GET/POST | `/api/admin/users` | **A** phase 6: keyset list (`?cursor=&limit=`); create bypasses `open_registration` |
| PATCH | `/api/admin/users/{id}/role` | **A** phase 6; refuses to demote the caller's own account |
| PATCH | `/api/admin/users/{id}/status` | **A** phase 6; refuses to disable the caller's own account |
| DELETE | `/api/admin/users/{id}` | **A** phase 6; refuses to delete the caller's own account |
| GET/PUT | `/api/admin/settings` | ○ **A** runtime settings in `setting` |
| GET/PUT | `/api/admin/access-rules` | ○ **A** provider/model permissions |
| GET | `/api/admin/usage` | ○ **A** usage and cost aggregates |

## Providers and models

| Method | Path | Notes |
|---|---|---|
| GET | `/api/providers` | secrets masked, health state inlined |
| POST | `/api/providers` | **A** from a preset; the key is encrypted before it is written |
| PATCH/DELETE | `/api/providers/{id}` | **A** interface-added providers only; configured ones answer 403 |
| GET | `/api/providers/presets` | catalogue from `providers/presets.toml` |
| POST | `/api/providers/probe` | test a base URL before saving; returns detected kind |
| POST | `/api/providers/autodiscover` | probe the well-known local ports |
| POST | `/api/providers/{id}/refresh` | force model rediscovery |
| GET | `/api/providers/{id}/health` | ○ cached; never blocks |
| GET | `/api/models` | unified list, local first, grouped, with capabilities, `supported_params` and `features` |
| PATCH | `/api/models/{provider_id}/{model_key}` | ○ rename / hide |
| GET | `/api/models/{provider_id}/{model_key}` | ○ full capability detail |
| GET/PUT/DELETE | `/api/model-params/{model_ref}` | the caller's saved parameters for one model, applied to every turn; parameters its backend does not accept are rejected |

### Local model management

Generic where possible, backed by the adapter's `LocalModelAdmin` capability.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/providers/{id}/local/models` | Ollama `/api/tags`, llama.cpp `/models` |
| GET | `/api/providers/{id}/local/models/{name}` | `/api/show`, `/props` |
| POST | `/api/providers/{id}/local/pull` | **S A** start or join a download job and stream it; `?detach=true` returns its snapshot (ADR-0017) |
| DELETE | `/api/providers/{id}/local/models/{name}` | `/api/delete` |
| POST | `/api/providers/{id}/local/copy` | `/api/copy` |
| POST | `/api/providers/{id}/local/create` | **S A** job: Modelfile text, or `from_model` plus overrides |
| GET | `/api/providers/{id}/local/running` | `/api/ps`, `/slots` — loaded + VRAM/RAM |
| POST | `/api/providers/{id}/local/unload` | **A** `keep_alive: 0` |
| GET | `/api/model-jobs` | **A** running jobs, then those finished in the last 15 minutes |
| GET | `/api/model-jobs/{job}/events` | **S A** follow a job: `progress` frames, then `done` |
| DELETE | `/api/model-jobs/{job}` | **A** cancel; Ollama resumes a cancelled pull later |

Reading (`models`, `models/{name}`, `running`) is open to any signed-in user; every change
is for administrators. A backend without the capability answers
`unsupported_capability`, and `GET /api/models` lists each provider's `features` so the
interface never has to try an operation to find out. Model names keep their `/` and `:`
in the path (`models/hf.co/org/model:Q4_K_M`).

## Chat

| Method | Path | Notes |
|---|---|---|
| GET | `/api/chats` | keyset: `?cursor=&limit=&folder=&tag=&archived=` (`tag` filter: **phase 6**) |
| POST | `/api/chats` | `custom_model_id` (**phase 6**): the client applies its system prompt and params to the creating turn |
| GET | `/api/chats/{id}` | the newest page of the active branch + `messages_cursor`; `?branch=&limit=` |
| PATCH | `/api/chats/{id}` | **phase 6**: rename, pin, archive, move — only the fields present in the body are touched |
| DELETE | `/api/chats/{id}` | soft delete |
| GET | `/api/chats/{id}/branch/{message_id}` | ○ switch the active branch |
| GET | `/api/chats/{id}/messages` | older pages: `?cursor=&limit=`, oldest first, explicit columns |
| POST | `/api/chats/{id}/completions` | **S** the hot path (see below) |
| GET | `/api/chats/{id}/stream?from=` | **S** read the turn already running; `0` replays it, `now` follows only what comes next (ADR-0024) |
| POST | `/api/chats/{id}/stop` | stop the running turn; `{"stopped": bool}` |
| POST | `/api/chats/{id}/messages/{mid}/regenerate` | ○ **S** new sibling |
| PATCH | `/api/chats/{id}/messages/{mid}` | ○ edit → new sibling branch |
| POST | `/api/chats/{id}/messages/{mid}/continue` | ○ **S** |
| POST | `/api/chats/{id}/title` | ○ regenerate the auto-title |
| GET | `/api/chats/{id}/export` | ○ JSON export |
| POST | `/api/chats/import` | ○ idempotent by chat id |
| GET | `/api/search?q=&cursor=` | **phase 6** FTS over titles and messages, dialect-neutral (see 02-db-schema.md) |
| POST | `/api/compare` | ○ **S** one prompt, N models, multiplexed stream |
| GET/POST | `/api/folders` | **phase 6** flat list; nesting via `parent_id` |
| PATCH/PUT/DELETE | `/api/folders/{id}`, `/api/folders/{id}/move` | **phase 6** rename; reparent + reorder; delete (children cascade, chats detach) |
| GET/POST | `/api/tags` | **phase 6** |
| PATCH/DELETE | `/api/tags/{id}` | **phase 6** |
| PUT/DELETE | `/api/tags/{id}/chats/{chat_id}` | **phase 6** attach / detach, idempotent |
| GET | `/api/tags/{id}/chats`, `/api/tags/for-chat/{id}` | **phase 6** |
| POST | `/api/chats/{id}/share` | ○ create a link |
| GET | `/api/share/{token}` | ○ **P** read-only rendering |

### The streaming endpoint

`POST /api/chats/{id}/completions` responds `text/event-stream`. The turn runs as its
own task and the response only reads it, so closing the response does not stop the
model: opening another conversation or reloading the page leaves the reply being
written, and `GET /api/chats/{id}/stream` picks it back up (ADR-0024). Stopping is
`POST /api/chats/{id}/stop`. A second turn in a conversation that already has one is a
409. The request carries
the parent message id, the user content, the model reference (or custom model slug),
per-turn parameter overrides, `knowledge_ids` (RAG), `tool_server_ids` (phase 8: MCP
server ids to offer tools from) and `web_tools` (bool: whether to also offer the
enabled builtin `"tools"` plugin's tools — web search and browsing, ADR-0014) — the
client resolves all three from the chat's custom model the same way. The response
event types are a closed set, versioned by the `event:` field:

```
event: start        {"message_id": "...", "model_ref": "...", "resolved_from": "..."}
event: status       {"phase": "loading_model"|"prompt_eval"|"generating"|"tool_wait"}
event: delta        {"t": "…text chunk…"}
event: reasoning    {"t": "…thinking chunk…"}
event: tool_call    {"id": "...", "name": "...", "args": {...}, "approval": "required"|"auto"}
event: tool_result  {"id": "...", "ok": true, "content": "..."}
event: citation     {"chunk_id": "...", "document_id": "...", "locator": {...}}
event: fallback     {"from": "...", "to": "...", "reason": "backend_offline"}
event: usage        {"tokens_in": 12, "tokens_out": 340, "cost_micros": 0,
                     "ttft_ms": 9, "tok_per_s": 41.2,
                     "prompt_eval_ms": 120, "eval_ms": 8200}
event: error        {"code": "context_overflow", "message": "...", "retryable": false}
event: done         {"finish_reason": "stop"}
```

As implemented (phase 8): a turn that offers tools can run several `tool_call`/
`tool_result` round trips before its `done`, one per tool the model calls, up to
`MAX_TOOL_ITERATIONS` (4) — see `services/chat.py` and
[ADR-0020](../adr/0020-mcp-transport-and-tool-calling-strategy.md). `tool_call.approval`
is `"required"` when the owning MCP server's `approval` mode (`always`/`once`, and
`once` for a tool not already approved this process) means the turn is now blocked on
`POST /api/tools/approve`; `"auto"` means it ran immediately — always the case for a
builtin plugin tool (`web_tools`), which has no approval gate at all. Only the turn's final
reply is persisted as a `message` row — the intermediate tool-call/result exchange is
not replayed on a later turn, but is attached to that row's `meta.tool_trace` so a
reopened chat can still show what happened (ADR-0020).

`delta` frames are the only ones on the hot path. They are emitted as pre-framed bytes
(`b"event: delta\ndata: {\"t\":"` + escaped text + `b"}\n\n"`) without building an
intermediate dict — see ADR-0004. A heartbeat comment (`: ping`) is sent every 15 s so
a 1 token/s backend never looks dead to a proxy.

Cancellation: closing the HTTP connection cancels the upstream request and marks the
message `stopped`; `POST /stop` does the same from another tab.

## Custom models and prompts

| Method | Path | Notes |
|---|---|---|
| GET | `/api/custom-models` | **phase 6** the caller's own, plus `shared`/`public` ones |
| POST | `/api/custom-models` | **phase 6** slug must be unique |
| GET/PATCH/DELETE | `/api/custom-models/{id}` | **phase 6** a private model owned by someone else answers 403/404 |
| GET/POST/PATCH/DELETE | `/api/prompts…` | ○ not yet implemented |

`tools`, `knowledge_ids`, `plugins` and `fallback_chain` are carried on `custom_model`.
`knowledge_ids` (phase 7) and `tools` (phase 8) are both acted on: `tools` is a list of
`mcp_server` ids, resolved into that server's cached tool list the same way
`knowledge_ids` is resolved into retrieved chunks — client-side, when starting a chat
from the custom model, then sent on `POST .../completions` as `tool_server_ids`.
`plugins` is a list of enabled plugin kinds (`"images"`, `"voice"` gate composer
buttons; `"tools"` is resolved into `web_tools: true` the same way `tool_server_ids`
is). `fallback_chain` is still carried but unused by any phase so far.

## Files and RAG

| Method | Path | Notes |
|---|---|---|
| POST | `/api/files` | multipart upload, sniffed type, size cap |
| GET/DELETE | `/api/files/{id}` | |
| GET/POST/PATCH/DELETE | `/api/collections…` | knowledge bases |
| POST | `/api/collections/{id}/documents` | multipart form (`file_id`, optional `title`); enqueues ingest, returns the document and job id (202) immediately |
| GET | `/api/collections/{id}/documents` | with per-document status/progress |
| DELETE | `/api/documents/{id}` | |
| POST | `/api/collections/{id}/query` | debug/preview retrieval |
| GET | `/api/rag-jobs/{id}/events` | **S** ingest/embed progress. Its own prefix rather than a shared `/api/jobs/{id}`: this codebase keeps one in-memory job registry per subsystem (`/api/model-jobs` already does the same for downloads, ADR-0017; ADR-0019 does the same for RAG) rather than a single job table/endpoint, so there is no shared registry to serve a generic path from. |
| POST | `/api/websearch` | runs the enabled builtin `"tools"` plugin's search directly (`501 unsupported_capability` if none is enabled); results are returned as-is, not ingested into a collection |

## Tools and MCP

| Method | Path | Notes |
|---|---|---|
| GET/POST/PATCH/DELETE | `/api/mcp/servers…` | **phase 8**; `auth_token` on create/update is encrypted at rest (ADR-0013), never echoed back except as `auth_hint: "set"` |
| POST | `/api/mcp/servers/{id}/connect` | **phase 8** connect + cache the tool list; a real handshake against the configured transport, not a stub |
| GET | `/api/mcp/servers/{id}/tools` | **phase 8** the cached list, no reconnect |
| POST | `/api/mcp/servers/import` | translates a Claude Code `mcpServers` config into one or more servers (`services/mcp_import.py`); credentials in `env`/`headers` land as plain config, not encrypted |
| POST | `/api/tools/approve` | **phase 8** approve or reject a pending tool call raised by a stream's `tool_call` event; `{call_id, approved, remember}` |
| GET | `/api/tools` | every tool the caller's enabled MCP servers currently cache, plus the enabled builtin `"tools"` plugin's own tools (`server_id: "builtin"`), if any |

## OpenAI-compatible gateway

velox-ui as a single gateway in front of local and cloud backends. Designed, not
built: every row below is marked **○**, and the paragraph after the table describes
how it is meant to work rather than how it works.

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/models` | ○ every visible model the caller may use |
| POST | `/v1/chat/completions` | ○ **S** streaming and non-streaming |
| POST | `/v1/embeddings` | ○ routed to the configured embedder/provider |
| POST | `/v1/completions` | ○ legacy text completion, best effort |

Authentication is a velox API key. This surface reuses the same service layer as the
UI path, so routing, fallback, quotas and usage accounting apply identically.

## Plugins

| Method | Path | Notes |
|---|---|---|
| GET | `/api/plugins` | discovered entry points and enable state |
| PUT | `/api/plugins/{name}` | **A** enable/disable (disabled = never imported) |
| POST | `/api/audio/transcribe` | STT, when the plugin is enabled |
| POST | `/api/audio/speech` | TTS |
| POST | `/api/images/generate` | image generation, disabled by default |

## Error envelope

Every non-streaming error returns the same shape, and streaming errors use the same
payload inside an `error` event:

```json
{"error": {"code": "rate_limited", "message": "Groq rate limit reached.",
           "provider": "groq", "model": "llama-3.3-70b", "retry_after_s": 12,
           "retryable": true, "request_id": "01J..."}}
```

`code` comes from a closed enum: `unauthorized`, `forbidden`, `not_found`,
`invalid_request`, `rate_limited`, `quota_exceeded`, `context_overflow`,
`model_not_found`, `backend_offline`, `backend_timeout`, `model_loading`,
`out_of_memory`, `invalid_credentials`, `unsupported_capability`,
`upstream_error`, `internal`.
