# ADR-0021: Image and voice plugins are OpenAI-compatible HTTP clients

**Status:** accepted
**Date:** 2026-09-15

## Context
Phase 10 adds optional image generation and voice (STT/TTS) plugins (ADR-0014). Both
capabilities are commonly served by heavy local models — diffusion models for images,
Whisper-class models for STT, neural TTS models for speech — which would pull torch or
an equivalent native stack into the base image, in direct conflict with ADR-0010's
no-torch stance and the <250MB image budget (docs/design/00-overview.md).

## Decision
The builtin `images` and `voice` plugins are thin `httpx` clients against
OpenAI-compatible routes on a user-configured base URL: `/v1/images/generations`,
`/v1/audio/transcriptions`, `/v1/audio/speech`. They share the process's existing HTTP
client (`providers/httpclient.py`) and credential encryption (`security/crypto.py`,
ADR-0013), but not `ProviderRegistry`/`ProviderSpec` — they are not chat-completion
adapters and need no model discovery or fallback chains.

The base URL can point at a cloud API (OpenAI) or at any self-hosted server that speaks
the same protocol (e.g. a local image-generation gateway, faster-whisper-server,
openedai-speech), the same shape as pointing a chat provider at a local Ollama host
(ADR-0008).

## Consequences
No local ASR/TTS/diffusion model ships in-process. A user who wants a fully offline
setup runs their own OpenAI-compatible server for that capability and points velox-ui
at it; velox-ui itself adds no new heavy dependency and no GPU/CPU inference code for
this phase.
