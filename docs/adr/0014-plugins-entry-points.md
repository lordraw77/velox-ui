# ADR-0014: Plugins via entry points, never imported when disabled

**Status:** proposed
**Date:** 2026-09-11

## Context
Image generation, STT, TTS and exotic providers must not cost cold-start time or RSS for
users who do not enable them.

## Decision
Five entry-point groups: `velox_ui.providers`, `velox_ui.tools`, `velox_ui.loaders`,
`velox_ui.embedders`, `velox_ui.stores`. Discovery reads entry-point *metadata* only; a
plugin's module is imported at first use, and never if it is disabled in config. Builtin
optional features are shipped as plugins using the same mechanism, so the fast path is
tested by the default configuration.

## Consequences
A plugin error surfaces at first use rather than at startup, so the plugin API includes a
`validate()` call that admins can run explicitly from the UI and from `velox config check`.
