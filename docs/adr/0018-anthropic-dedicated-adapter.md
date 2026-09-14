# ADR-0018: Anthropic gets a dedicated adapter, not a preset

**Status:** accepted — implemented in phase 5
**Date:** 2026-09-14

## Context
ADR-0009 routes every cloud provider through the single `openai_compat` adapter, driven
by a preset. That held for OpenAI, Groq, OpenRouter, Mistral, NVIDIA NIM, Cloudflare
Workers AI and Gemini — all of them publish (or officially proxy to) the OpenAI chat-
completions wire format. Anthropic does not: `POST /v1/messages`, not
`/chat/completions`; a named SSE envelope (`event: content_block_delta`, not a
`data:`-only stream); a `max_tokens` field the API rejects a request for omitting; and a
system prompt that is a top-level field, never a message with `role: "system"`.

Extended thinking adds a second reason. Anthropic's `thinking` request object pins
`temperature` to unset and requires `max_tokens` to exceed its `budget_tokens` — turning
it on is not one independent field the way Ollama's `think` is, and forcing it through
the interface's generic reasoning toggle would silently change what other saved
parameters do to a request.

## Decision
`providers/anthropic.py` is a second full adapter, matching the `Provider` protocol
structurally like every other one. The registry's `_build` gets one more branch, on
`kind == "anthropic"` rather than on preset identity — the preset system still supplies
the base URL, label and docs link, but not the wire format.

The interface's reasoning toggle (`SamplingParams.think`) does nothing for this adapter;
it is simply absent from `supported_params`, so the control never appears for an
Anthropic backend. What the adapter does unconditionally is relay any `thinking` content
block a model streams on its own — extended-thinking models do this without being asked
— exactly like Ollama's separated `message.thinking`, so such a model is never read as
having gone silent.

## Consequences
- A ninth cloud backend that turns out to be OpenAI-compatible is a TOML entry. A tenth
  that is not — something with a wire format of its own — is a third dedicated adapter,
  and the two existing ones (this and Ollama's) are the template for it.
- `max_tokens` always has a value on the wire even when nothing was saved
  (`_DEFAULT_MAX_TOKENS = 4096`), because the Messages API has no server-side default
  the way OpenAI-compatible backends do.
- Anthropic has no embeddings endpoint; `embed()` raises `UnsupportedCapability` rather
  than silently returning nothing.
- Structured output (`ChatRequest.json_schema`) and grammars are not encoded: Anthropic
  has no equivalent field in the Messages API, only tool-forced JSON, which is not
  wired here. Nothing in the product surface sets `json_schema` yet, so this costs
  nothing today and is worth revisiting once structured output is.
