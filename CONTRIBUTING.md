# Contributing to velox-ui

## Development setup

```
uv sync --extra dev --extra postgres --extra uvicorn
cd frontend && npm ci
```

`uv run velox serve` starts the backend against a zero-config SQLite database
in `./data`. The frontend dev server (`cd frontend && npm run dev`) proxies
API requests to it; for a single build embedded in the wheel, `npm run
build` writes into `src/velox_ui/web/`.

## Running tests

```
pytest                    # unit, integration and contract tests
pytest -m contract        # provider/plugin adapters against fake backends only
python -m bench           # the performance gates (ADR-0015)
cd frontend && npm test   # i18n coverage, virtual list, markdown, streaming sink
```

No test requires a network, an API key or a real inference backend — contract
tests run against deterministic fakes in `tests/fakes/`, on a real localhost
socket (`tests/fakes/server.py`), not an in-process ASGI transport, so
streaming behaviour is exercised honestly.

## Code style

- `ruff check .` and `ruff format --check .` (line length 96, both enforced
  in CI).
- `mypy` in strict mode over `src/velox_ui`.
- Docstrings on every public function, class and module; comments only where
  the *why* isn't obvious from the code.
- English everywhere in the repository — code, docs, commit messages, UI
  strings (ADR-0016). UI strings live in `frontend/src/lib/i18n/en.json`,
  never hardcoded in a component; `it.json` is the first translation and may
  lag behind without breaking anything (a missing key falls back to English).

## Architecture

Start with `docs/design/00-overview.md` and `docs/design/01-repo-layout.md`
for the layering rule and the import-cost rule. The short version: `api/`
calls `services/`, `services/` calls `providers/`/`rag/`/`mcp/`/`db/`, and
nothing below `services/` imports from `api/`. Optional subsystems (`rag/`,
`mcp/`, `plugins/builtin/`) are imported at first use, never at startup,
unless configured on.

Design decisions with lasting consequences are recorded as ADRs under
`docs/adr/`. Propose one (copy `docs/adr/0000-template.md`) for anything that
changes the framework, storage, protocol or plugin surface — not for routine
feature work.

## Adding a provider

See `docs/adding-a-provider.md`: most backends need only a preset entry in
`providers/presets.toml` over the parametrized OpenAI-compatible adapter; a
genuinely different protocol gets its own adapter implementing the `Provider`
Protocol in `providers/base.py`.

## Adding a plugin

Plugins are discovered by entry-point metadata and imported only when
enabled (ADR-0014) — `src/velox_ui/plugins/spec.py` has the Protocols
(`Plugin`, `ImagePlugin`, `VoicePlugin`), `plugins/loader.py` has discovery
and lazy loading. A new plugin kind needs: a `velox_ui.<kind>` entry-point
group declared in an ADR extending ADR-0014, a config/credential shape
following `plugins/spec.py::PluginConfig`, and a route exposing it — see
`plugins/builtin/images/` and `plugins/builtin/voice/` for the reference
implementations, both thin HTTP clients with no bundled model (ADR-0021).

## Commits and pull requests

- Commit messages explain *why*, not *what* — the diff already shows what
  changed.
- Keep a pull request to one coherent change; a bug fix does not need
  surrounding cleanup.
- New code ships with tests at the layer that actually exercises it: pure
  logic in `tests/unit/`, an HTTP surface in `tests/integration/`, an
  external protocol in `tests/contract/`.
