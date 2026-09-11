# ADR-0001: FastAPI as the web framework, with msgspec on the hot path

**Status:** accepted
**Date:** 2026-09-11

## Context
The brief allows FastAPI or Litestar. The hot path must avoid Pydantic validation and
reach the response body with minimal per-request work. Litestar has first-class msgspec
support and a lower baseline per-request overhead; FastAPI couples routing to Pydantic
and builds a validation pipeline per handler. Against that, FastAPI is by far the more
widely known framework, which matters for contribution and long-term maintainability.

## Decision
Use **FastAPI**, and keep Pydantic out of the latency-critical path by construction
rather than by discipline:

- **Streaming endpoints** (`/api/chats/{id}/completions`, `/api/compare`,
  `/v1/chat/completions`, SSE progress endpoints) declare **no** `response_model` and
  return a `StreamingResponse` over a byte iterator. Request bodies on these routes are
  read as raw bytes and decoded with `msgspec.json.decode` into a `Struct`, so no
  Pydantic model is constructed per turn.
- **Hot read endpoints** (chat list, message list, model list) return a pre-encoded
  `Response(content=msgspec.json.encode(...), media_type="application/json")`. No
  `response_model`, no `jsonable_encoder`.
- **Everything else** (auth, admin, provider CRUD, settings) uses ordinary Pydantic v2
  models, where rich validation and generated OpenAPI documentation are worth the cost.
- OpenAPI descriptions for the msgspec routes are supplied explicitly via `responses=`
  and `openapi_extra=` so the documented surface stays complete.
- A test asserts that no route reachable during a completion turn declares a
  `response_model`, so the boundary cannot erode silently.

## Consequences
We accept a slightly higher framework baseline than Litestar would give us, and we carry
two serialization styles in one codebase — a real cost, paid for a much larger pool of
contributors and examples. The msgspec/Pydantic boundary is a convention that needs a
lint-style test to hold, which is why that test lands in phase 1 rather than later.
`bench/cases/ttft_overhead.py` measures the framework's contribution to the 15 ms
budget; if FastAPI's baseline alone consumes it, this ADR is revisited in favour of
Litestar.
