# ADR-0006: Conversations are trees, not lists

**Status:** proposed
**Date:** 2026-09-11

## Context
Editing a message and regenerating an answer must not destroy history, and the UI must
show "2 / 3" sibling navigation like Open WebUI.

## Decision
`message.parent_id` forms a tree per chat; `chat.active_leaf_id` records the current
branch tip. Edit and regenerate insert a sibling and move the tip. `message.depth` is
denormalized so the active path can be fetched with one indexed range scan and
reassembled in memory. The API returns the active path plus sibling counts; other
branches are fetched on demand.

## Consequences
Slightly more complex reads than a flat list, and `depth` must be maintained on insert.
In exchange, branching, export and import are lossless, and the 5 000-message open stays
a single scan.
