# ADR-0005: Persistence runs beside the stream, never in front of it

**Status:** proposed
**Date:** 2026-09-11

## Context
Writing every token to the DB, or writing the assistant message before forwarding the
first token, adds latency and write amplification.

## Decision
The turn is persisted by a background writer:
- The user message and an empty assistant row (`status='streaming'`) are written
  *concurrently* with opening the upstream request, not before it; the message id is a
  client-side ULID so streaming can start before the write lands.
- Token text is appended to an in-task `bytearray`, flushed to the DB at most every
  N milliseconds (default 750) and at stream end, as a single `UPDATE`.
- `usage_event`, `timings`, token counts and the chat's `updated_at`/`message_count` are
  written once, after `done`, from the writer queue.
- If the client disconnects, the writer still finishes and marks the message `stopped`,
  so the partial answer is never lost.

## Consequences
A crash mid-stream can lose up to one flush interval of text; the message is then found
in `streaming` state at startup and is repaired to `stopped`. This is an explicit
trade-off in favour of latency.
