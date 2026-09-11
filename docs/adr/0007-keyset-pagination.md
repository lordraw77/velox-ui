# ADR-0007: Keyset pagination everywhere

**Status:** proposed
**Date:** 2026-09-11

## Context
`OFFSET` degrades linearly; a user with 10 000 chats must not pay for it, and the target
is < 30 ms for the chat list.

## Decision
Every list endpoint takes an opaque `cursor` (base64 msgpack of the ordering tuple) and
a `limit`. Chat listing orders by `(pinned DESC, updated_at DESC, id DESC)` and is
served by the partial index `ix_chat_list`. Message listing orders by `(depth, id)`.
`OFFSET` is banned outside admin reports.

## Consequences
No "jump to page 47" UI; the frontend uses infinite scrolling, which is what the design
wants anyway. Cursors are opaque so the ordering can change without breaking clients.
