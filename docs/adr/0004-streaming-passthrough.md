# ADR-0004: Byte-oriented streaming pass-through

**Status:** proposed
**Date:** 2026-09-11

## Context
TTFT overhead must stay under 15 ms p95, and a Groq-class backend can emit 500+ tok/s.
The naive pipeline (parse upstream JSON -> build a dict -> validate -> re-serialize ->
frame as SSE) costs several allocations and two JSON passes per token.

## Decision
Three tiers, chosen per adapter:

1. **Raw pass-through** (`RawPassthrough`): when the upstream wire format is already our
   delta format (OpenAI-compatible backends), forward upstream bytes unchanged and tee a
   copy to a cheap scanner that extracts text for persistence and usage for metrics. No
   re-serialization at all.
2. **Reframe** (Ollama, llama.cpp, Anthropic, Gemini): decode each chunk into a tiny
   msgspec `Struct` with only the fields we need, then emit a pre-built SSE frame by
   concatenating cached byte literals with the JSON-escaped text. One decode, no dict,
   no full re-encode.
3. **Fallback**: generic decode/encode, used only by adapters that cannot do better.

The response body is never accumulated in memory. The client-facing writer sets
`Cache-Control: no-store`, `X-Accel-Buffering: no`, and flushes per chunk.

## Consequences
Two code paths for text extraction (scanner and struct decode) that must agree; the
contract test suite asserts they produce identical persisted text for the same
transcript. The scanner is format-specific and is the fiddliest code in the project, so
it lives in one file with exhaustive tests.
