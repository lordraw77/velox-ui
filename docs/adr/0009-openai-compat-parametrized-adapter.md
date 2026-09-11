# ADR-0009: One parametrized adapter for OpenAI-compatible backends

**Status:** proposed
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
