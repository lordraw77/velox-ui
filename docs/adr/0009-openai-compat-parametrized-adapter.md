# ADR-0009: One parametrized adapter for OpenAI-compatible backends

**Status:** accepted — implemented in phase 4
**Date:** 2026-09-11

## Context
Ten of the required backends speak the same protocol with small deviations. One adapter
per backend would duplicate the streaming loop ten times.

## Decision
A single `openai_compat` adapter parametrized by a preset: base URL, auth style, header
set, and a `quirks` table (usage in the final chunk, missing usage entirely, `stop` array
handling, non-standard role names, `/models` shape). Presets live in
`providers/presets.toml` and are data, not code. Only Ollama, llama.cpp, Gemini,
Anthropic, Mistral and Cloudflare's native route get dedicated adapters, because their
protocols genuinely differ.

## Consequences
Adding LM Studio, vLLM, TGI, TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile or mlx_lm is a
TOML entry plus a contract-test transcript. A backend with a quirk we have not modelled
needs a new quirk flag, and a truly incompatible one falls back to a dedicated adapter.

## Implementation notes (phase 4)
The quirks that turned out to matter were narrower than the list above. Usage arrives in
a chunk with an empty `choices` array when `stream_options.include_usage` is sent, and
that option is a preset flag for servers that reject it. Reasoning appears in either
`delta.reasoning_content` or `delta.reasoning` and both are read unconditionally. The
real divergence is sampling parameters: which ones a server accepts beyond the OpenAI set
and what it calls them (`repetition_penalty`, `typical`, `mirostat_mode`), which presets
express as `sampling` and `rename`. The contract suite replays the chunk format, tool-call
fragments that carry their id only on the first fragment, llama.cpp-style `timings`, and
the error bodies vLLM, TGI and OpenAI return.
