# ADR-0002: Granian as the default ASGI server

**Status:** proposed
**Date:** 2026-09-11

## Context
Cold start < 1 s, idle RSS < 150 MB, and low per-chunk overhead when streaming SSE.
uvicorn+uvloop+httptools is the safe default; Granian (Rust HTTP layer) starts faster,
uses less memory per worker, and has lower framing overhead on many small writes.

## Decision
Ship Granian as the default server, keep uvicorn+uvloop+httptools as a supported
fallback selected by `VELOX_SERVER=uvicorn`. Both paths are exercised in CI.

## Consequences
One extra binary dependency with platform wheels. Keeping both servers working costs a
little CI time but removes deployment risk. Default worker count is 1: multiple workers
would fragment the in-process LRU caches and the SQLite writer, and add RSS.
