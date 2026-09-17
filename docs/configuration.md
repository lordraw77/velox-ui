# Configuration

velox-ui runs with no configuration at all — a zero-config start uses SQLite
under `./data`, probes the conventional local inference ports, and needs no
credential. `velox.example.toml` is a fully commented copy of every setting
and its default; copy it to `velox.toml` only to change something.

## Precedence

An environment variable always wins over `velox.toml`, which always wins
over the built-in default. The variable name is `VELOX_` followed by the
setting's path, joined by underscores and uppercased:

```
port                    -> VELOX_PORT
db.url                  -> VELOX_DB_URL
auth.access_token_ttl_s -> VELOX_AUTH_ACCESS_TOKEN_TTL_S
```

`velox config check` validates the effective configuration — file plus
environment — and prints it, without starting the server.

## Top level

| Key | Default | Notes |
|---|---|---|
| `host` | `0.0.0.0` | Self-hosted default; use `127.0.0.1` to restrict to this machine. |
| `port` | `8080` | |
| `data_dir` | `./data` | Database, uploads, generated secret key. |
| `server` | `granian` | Or `uvicorn` (`VELOX_SERVER=uvicorn`). |
| `workers` | `1` | Deliberate: more would fragment in-process caches and the SQLite writer (ADR-0002). |
| `log_level` / `log_format` | `INFO` / `json` | `console` for a terminal. |
| `secret_key` | *(generated)* | Encrypts stored credentials, signs tokens (ADR-0013). Empty reads or creates `data_dir/secret.key`. Back it up with the database — losing it means re-entering every credential. |
| `cors_origins` | `()` | Extra browser origins; the bundled interface needs none. |

## `[db]`

Empty `url` means SQLite inside `data_dir`, zero setup. For PostgreSQL:
`postgresql+asyncpg://user:pass@host:5432/db` (needs the `postgres` extra,
`pip install velox-ui[postgres]`). `auto_migrate` (default on) applies
pending Alembic migrations at startup; turn it off only when a deployment
runs `velox migrate up` out of band.

## `[auth]`

`enabled = false` collapses every request onto one built-in local account —
the single-user desktop case, no login screen. With auth on, the first
account to register is always the administrator; `open_registration`
controls whether anyone else can (an administrator can otherwise create
accounts directly, see `docs/design/03-http-api.md`'s admin routes).
`admin_email`/`admin_password` bootstrap that first account non-interactively
(leave `admin_password` empty to let the first real registration claim it
instead — no password is generated or logged).

## `[metrics]`

Prometheus exposition at `/metrics`, on by default. `require_auth` gates it
behind an administrator token; off by default because the endpoint is
normally reachable only from inside the deployment network.

## `[providers]`

Backends configured here are **read-only in the interface** — a change to
`velox.toml` is never silently overridden by a UI edit — while backends
added from the interface are stored in the database and editable there. Both
kinds coexist. `ollama_hosts` / `llamacpp_hosts` are the shorthand for those
two native adapters; any other backend is a `[[providers.endpoints]]` entry
naming a preset from `providers/presets.toml` (`docs/providers.md` has the
full catalogue). `autodiscover` (default on) probes the conventional local
ports once, only when nothing is configured, and never touches the LAN.

Per-preset environment variables: `VELOX_PROVIDER_<PRESET>_HOSTS` and
`VELOX_PROVIDER_<PRESET>_API_KEY`, e.g. `VELOX_PROVIDER_VLLM_HOSTS`,
`VELOX_PROVIDER_ANTHROPIC_API_KEY`.

## Plugins (images, voice, tools)

Not part of `velox.toml` — these are runtime singletons (ADR-0014), disabled
by default, configured through the interface's **Plugins** page or directly:

```
PUT /api/plugins/images  {"enabled": true, "base_url": "...", "model": "...", "api_key": "..."}
PUT /api/plugins/voice   {"enabled": true, "base_url": "...", "model": "...", "tts_voice": "...", "api_key": "..."}
PUT /api/plugins/tools   {"enabled": true, "base_url": "..."}
```

Images and voice point at an OpenAI-compatible host — cloud or self-hosted —
for `/v1/images/generations`, `/v1/audio/transcriptions` and
`/v1/audio/speech` (ADR-0021); neither ships a bundled model. The API key,
if any, is encrypted at rest the same way a provider's is.

`tools` is a group rather than a single backend: every plugin registered under
the `velox_ui.tools` entry point contributes whatever tools its configuration
supports. `current_datetime` (the host's date, weekday, timezone and UTC) needs
no configuration at all, so enabling `tools` with an empty `base_url` gives you
that on its own. Setting `base_url` to a self-hosted SearXNG instance adds
`web_search` and `web_browse` on top. None of them has an approval gate — they
run immediately when the model calls them.

SearXNG is reached directly, not through an MCP gateway. `docker-compose.mcp-multi.yml`
runs one on the compose network, reachable as `http://searxng:8080`, with
`docker/searxng/settings.yml`. The official image does not work with velox-ui
unmodified, in two ways worth knowing before debugging either:

- it enables only the `html` output format, so the plugin's
  `/search?format=json` answers **403** until `json` is added to
  `search.formats`;
- mounting a settings file drops the key the image would otherwise generate, and
  SearXNG then **exits at startup** with `server.secret_key is not changed`.
  Pass the key as `SEARXNG_SECRET` (kept in `.env`) rather than committing it.

Tools are offered to every chat once the group is enabled. A custom model can
also request them explicitly by including `"tools"` in its `plugins` list (the
"Builtin tools" checkbox in **Custom models**), which the client resolves into
`web_tools: true` on the completion request, the same pattern `tool_server_ids`
already uses for MCP servers.

## Secrets

`VELOX_SECRET_KEY` (or the generated `data_dir/secret.key`) is the one key
that matters for continuity: it decrypts every stored provider, MCP server
and plugin credential. Rotating it without migrating existing ciphertext
makes `SecretDecryptionError` the answer to every stored credential — the
error message says so and asks for the credential to be re-entered, rather
than failing silently.
