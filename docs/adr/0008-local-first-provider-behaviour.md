# ADR-0008: Local backends are first-class, not a degraded cloud

**Status:** proposed
**Date:** 2026-09-11

## Context
A local host may be off, slow, unauthenticated, or busy loading 30 GB of weights. Cloud
assumptions (an API key exists, the first token arrives in a second, a timeout means
failure, a retry is free) are wrong for it and produce a bad experience.

## Decision
- Auth is optional; a provider with no key produces no warning and no error.
- Timeouts are split: connect, first-token (default 600 s) and between-tokens
  (default 120 s). No total-duration timeout by default.
- `ModelLoading` is a distinct state surfaced as `status: loading_model` in the stream
  and rendered distinctly in the UI. It is never retried.
- Health checks are cheap, cached, run out of band, and a `down` host degrades to an
  offline badge; it never slows a page load and logs at most once per state change.
- First-run autodiscovery probes `localhost:11434`, `:8080`, `:1234`, `:8000` with a
  short timeout and configures what answers. LAN scanning exists but is manual only.
- Capabilities are read from the backend (`/api/show`, `/props`), never inferred from
  the model name.
- Local models sort first in the picker and display a cost of exactly zero.

## Consequences
More states to model in the UI than a cloud-only client would need, and a slightly more
complex timeout configuration. This is the project's differentiator, so the complexity
is paid deliberately.
