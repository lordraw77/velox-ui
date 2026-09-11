# ADR-0012: Svelte 5, virtual scrolling, markdown in a worker

**Status:** proposed
**Date:** 2026-09-11

## Context
The bundle budget is 200 KB gzip, a 5 000-message chat must open in 150 ms, and
rendering must not stutter while tokens stream in — including on a client that is also
running the model.

## Decision
Svelte 5 (runes) + Vite + TypeScript. No component library; a small hand-written
design system. Messages render through a variable-height virtual list anchored to the
bottom. Streaming text goes into a single mutable sink flushed on `requestAnimationFrame`
(throttled, coalescing multiple tokens per frame), touching only the last message node.
Markdown, syntax highlighting and KaTeX run in a web worker on completed blocks; the
streaming tail is rendered as plain text until its block closes. Highlight languages and
KaTeX load lazily.

## Consequences
Slightly delayed formatting of the final block, which is invisible in practice and is the
price of never re-parsing the whole conversation. The worker adds a build target and a
message protocol to maintain.
