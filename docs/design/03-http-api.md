# HTTP API

Design document. All paths are prefixed `/api` except the OpenAI-compatible surface
(`/v1`), system endpoints and static assets. Request/response bodies are JSON encoded
with msgspec. Authentication is a bearer JWT or an API key (`Authorization: Bearer …`);
public share routes are unauthenticated by design.

Legend: **A** = admin only, **P** = public (no auth), **S** = streaming.

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
| GET | `/api/auth/oidc/login` | **P** optional OIDC start |
| GET | `/api/auth/oidc/callback` | **P** |
| GET/PATCH | `/api/me` | profile + UI settings |
| GET/POST | `/api/me/api-keys` | key is returned once, on creation |
| DELETE | `/api/me/api-keys/{id}` | |
| GET/POST/PATCH/DELETE | `/api/admin/users…` | **A** |
| GET/PUT | `/api/admin/settings` | **A** runtime settings in `setting` |
| GET/PUT | `/api/admin/access-rules` | **A** provider/model permissions |
| GET | `/api/admin/usage` | **A** usage and cost aggregates |

## Providers and models

| Method | Path | Notes |
|---|---|---|
| GET | `/api/providers` | secrets masked, health state inlined |
| POST | `/api/providers` | |
| PATCH/DELETE | `/api/providers/{id}` | |
| GET | `/api/providers/presets` | catalogue from `providers/presets.toml` |
| POST | `/api/providers/probe` | test a base URL before saving; returns detected kind |
| POST | `/api/providers/autodiscover` | probe the well-known local ports |
| POST | `/api/providers/{id}/refresh` | force model rediscovery |
| GET | `/api/providers/{id}/health` | cached; never blocks |
| GET | `/api/models` | unified list, local first, grouped, with capabilities |
| PATCH | `/api/models/{provider_id}/{model_key}` | rename / hide |
| GET | `/api/models/{provider_id}/{model_key}` | full capability detail |

### Local model management

Generic where possible, backed by the adapter's `LocalModelAdmin` capability.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/providers/{id}/local/models` | Ollama `/api/tags`, llama.cpp `/models` |
| GET | `/api/providers/{id}/local/models/{name}` | `/api/show`, `/props` |
| POST | `/api/providers/{id}/local/pull` | **S** SSE pull progress (bytes, layers, %) |
| DELETE | `/api/providers/{id}/local/models/{name}` | `/api/delete` |
| POST | `/api/providers/{id}/local/copy` | `/api/copy` |
| POST | `/api/providers/{id}/local/create` | create from a Modelfile |
| GET | `/api/providers/{id}/local/running` | `/api/ps`, `/slots` — loaded + VRAM/RAM |
| POST | `/api/providers/{id}/local/unload` | `keep_alive: 0` |

## Chat

| Method | Path | Notes |
|---|---|---|
| GET | `/api/chats` | keyset: `?cursor=&limit=&folder=&tag=&archived=` |
| POST | `/api/chats` | |
| GET | `/api/chats/{id}` | active path + sibling counts; `?depth_from=` for paging |
| PATCH/DELETE | `/api/chats/{id}` | rename, pin, archive, move, soft delete |
| GET | `/api/chats/{id}/branch/{message_id}` | switch the active branch |
| GET | `/api/chats/{id}/messages` | keyset within one chat, explicit columns |
| POST | `/api/chats/{id}/completions` | **S** the hot path (see below) |
| POST | `/api/chats/{id}/messages/{mid}/regenerate` | **S** new sibling |
| PATCH | `/api/chats/{id}/messages/{mid}` | edit → new sibling branch |
| POST | `/api/chats/{id}/messages/{mid}/continue` | **S** |
| POST | `/api/chats/{id}/stop` | cancels the in-flight turn |
| POST | `/api/chats/{id}/title` | regenerate the auto-title |
| GET | `/api/chats/{id}/export` | JSON export |
| POST | `/api/chats/import` | idempotent by chat id |
| GET | `/api/search?q=&cursor=` | FTS over titles and messages |
| POST | `/api/compare` | **S** one prompt, N models, multiplexed stream |
| GET/POST/PATCH/DELETE | `/api/folders…`, `/api/tags…` | |
| POST | `/api/chats/{id}/share` | create a link |
| GET | `/api/share/{token}` | **P** read-only rendering |

### The streaming endpoint

`POST /api/chats/{id}/completions` responds `text/event-stream`. The request carries
the parent message id, the user content, the model reference (or custom model slug),
per-turn parameter overrides and tool/RAG toggles. The response event types are a
closed set, versioned by the `event:` field:

```
event: start        {"message_id": "...", "model_ref": "...", "resolved_from": "..."}
event: status       {"phase": "loading_model"|"prompt_eval"|"generating"|"tool_wait"}
event: delta        {"t": "…text chunk…"}
event: reasoning    {"t": "…thinking chunk…"}
event: tool_call    {"id": "...", "name": "...", "args": {...}, "approval": "required"}
event: tool_result  {"id": "...", "ok": true, "content": "..."}
event: citation     {"chunk_id": "...", "document_id": "...", "locator": {...}}
event: fallback     {"from": "...", "to": "...", "reason": "backend_offline"}
event: usage        {"tokens_in": 12, "tokens_out": 340, "cost_micros": 0,
                     "ttft_ms": 9, "tok_per_s": 41.2,
                     "prompt_eval_ms": 120, "eval_ms": 8200}
event: error        {"code": "context_overflow", "message": "...", "retryable": false}
event: done         {"finish_reason": "stop"}
```

`delta` frames are the only ones on the hot path. They are emitted as pre-framed bytes
(`b"event: delta\ndata: {\"t\":"` + escaped text + `b"}\n\n"`) without building an
intermediate dict — see ADR-0004. A heartbeat comment (`: ping`) is sent every 15 s so
a 1 token/s backend never looks dead to a proxy.

Cancellation: closing the HTTP connection cancels the upstream request and marks the
message `stopped`; `POST /stop` does the same from another tab.

## Custom models and prompts

| Method | Path |
|---|---|
| GET/POST/PATCH/DELETE | `/api/custom-models…` |
| GET/POST/PATCH/DELETE | `/api/prompts…` |

## Files and RAG

| Method | Path | Notes |
|---|---|---|
| POST | `/api/files` | multipart upload, sniffed type, size cap |
| GET/DELETE | `/api/files/{id}` | |
| GET/POST/PATCH/DELETE | `/api/collections…` | knowledge bases |
| POST | `/api/collections/{id}/documents` | enqueues ingest, returns job id immediately |
| GET | `/api/collections/{id}/documents` | with per-document status/progress |
| DELETE | `/api/documents/{id}` | |
| POST | `/api/collections/{id}/query` | debug/preview retrieval |
| GET | `/api/jobs/{id}` | **S** optional SSE progress |
| POST | `/api/websearch` | run a configured search provider as a RAG source |

## Tools and MCP

| Method | Path | Notes |
|---|---|---|
| GET/POST/PATCH/DELETE | `/api/mcp/servers…` | |
| POST | `/api/mcp/servers/{id}/connect` | connect + cache the tool list |
| GET | `/api/mcp/servers/{id}/tools` | |
| POST | `/api/tools/approve` | approve or reject a pending tool call |
| GET | `/api/tools` | all tools available to the caller (MCP + builtin + plugins) |

## OpenAI-compatible gateway

velox-ui as a single gateway in front of local and cloud backends.

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/models` | every visible model the caller may use |
| POST | `/v1/chat/completions` | **S** streaming and non-streaming |
| POST | `/v1/embeddings` | routed to the configured embedder/provider |
| POST | `/v1/completions` | legacy text completion, best effort |

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
