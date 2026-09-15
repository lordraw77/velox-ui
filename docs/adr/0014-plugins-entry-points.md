# ADR-0014: Plugins via entry points, never imported when disabled

**Status:** accepted
**Date:** 2026-09-11

## Context
Image generation, STT, TTS and exotic providers must not cost cold-start time or RSS for
users who do not enable them.

## Decision
Seven entry-point groups: `velox_ui.providers`, `velox_ui.tools`, `velox_ui.loaders`,
`velox_ui.embedders`, `velox_ui.stores`, `velox_ui.images`, `velox_ui.voice`. Discovery
reads entry-point *metadata* only; a plugin's module is imported at first use, and never
if it is disabled in config. Builtin optional features are shipped as plugins using the
same mechanism, so the fast path is tested by the default configuration.

Phase 10 is the first real consumer of this mechanism (`src/velox_ui/plugins/`): a
builtin image-generation plugin and a builtin voice (STT+TTS) plugin register under
`velox_ui.images` and `velox_ui.voice` via this repository's own `pyproject.toml`, so
the default, both-disabled path exercises the same discovery code a third-party plugin
would. `velox_ui.voice` covers both transcription and speech with one plugin class
(one entry point) rather than splitting into separate `stt`/`tts` groups, since both
capabilities are operationally one backend connection (ADR-0021).

## Consequences
A plugin error surfaces at first use rather than at startup, so the plugin API includes a
`validate()` call that admins can run explicitly from the UI and from `velox config check`.
