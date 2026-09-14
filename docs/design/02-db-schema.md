# Database schema

Design document. Dialects: SQLite (default, WAL, aiosqlite) and PostgreSQL (asyncpg).
DDL below is written in a dialect-neutral style; per-dialect notes are called out.

## Conventions

- Primary keys are 26-char ULIDs stored as `TEXT`/`CHAR(26)`: monotonic (index-friendly),
  sortable by creation time, generated client-side so no round-trip is needed to know a
  message id before streaming starts.
- Timestamps are `INTEGER` epoch milliseconds (UTC). Cheap to compare, no tz ambiguity,
  no per-dialect datetime adapter in the hot path.
- Free-form structured data uses a `meta` column: `JSON`/`JSONB` on Postgres, `BLOB`
  msgpack on SQLite (msgspec-encoded). Never queried in a hot path.
- No `SELECT *` on `message`; repositories select explicit column tuples.
- Soft delete only where the UI needs undo (`chat.deleted_at`); everything else is hard
  delete with `ON DELETE CASCADE`.

## Identity and access

```sql
CREATE TABLE app_user (
  id            CHAR(26) PRIMARY KEY,
  email         TEXT NOT NULL,
  email_norm    TEXT NOT NULL UNIQUE,       -- lowercased, used for lookup
  name          TEXT NOT NULL,
  password_hash TEXT,                       -- NULL when the account is OIDC-only
  role          TEXT NOT NULL,              -- 'admin' | 'user'
  status        TEXT NOT NULL,              -- 'active' | 'pending' | 'disabled'
  avatar_url    TEXT,
  settings      BLOB,                       -- UI prefs: theme, locale, shortcuts
  created_at    INTEGER NOT NULL,
  last_seen_at  INTEGER
);

CREATE TABLE oidc_identity (
  id         CHAR(26) PRIMARY KEY,
  user_id    CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  issuer     TEXT NOT NULL,
  subject    TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (issuer, subject)
);

CREATE TABLE refresh_token (
  id         CHAR(26) PRIMARY KEY,
  user_id    CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,          -- SHA-256 of the opaque token
  family_id  CHAR(26) NOT NULL,             -- rotation family, for reuse detection
  expires_at INTEGER NOT NULL,
  revoked_at INTEGER,
  user_agent TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX ix_refresh_user ON refresh_token(user_id, expires_at);

CREATE TABLE api_key (
  id          CHAR(26) PRIMARY KEY,
  user_id     CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  prefix      TEXT NOT NULL,                -- first 8 chars, shown in the UI
  key_hash    TEXT NOT NULL UNIQUE,
  scopes      BLOB,
  last_used_at INTEGER,
  expires_at  INTEGER,
  created_at  INTEGER NOT NULL
);
CREATE INDEX ix_apikey_user ON api_key(user_id);

-- Per-user limits; absent row means "inherit global config".
CREATE TABLE user_quota (
  user_id            CHAR(26) PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
  requests_per_min   INTEGER,
  tokens_per_day     INTEGER,
  cost_cents_per_day INTEGER
);

-- Which users/roles may use which provider or model. Absence = allowed (local-first).
CREATE TABLE access_rule (
  id           CHAR(26) PRIMARY KEY,
  subject_kind TEXT NOT NULL,               -- 'role' | 'user'
  subject_id   TEXT NOT NULL,               -- role name or user id
  object_kind  TEXT NOT NULL,               -- 'provider' | 'model'
  object_id    TEXT NOT NULL,
  effect       TEXT NOT NULL,               -- 'allow' | 'deny'
  UNIQUE (subject_kind, subject_id, object_kind, object_id)
);
```

## Providers and models

```sql
-- Only providers added in the interface are stored. Those from configuration or
-- autodiscovery are rebuilt at every start, so an edited velox.toml never competes
-- with a stale copy — and there is no `origin` column to keep in sync.
CREATE TABLE provider (
  id            VARCHAR(40) PRIMARY KEY,    -- readable slug; first half of model_ref
  name          TEXT NOT NULL,              -- user-visible label
  kind          TEXT NOT NULL,              -- 'ollama' | 'llamacpp' | 'openai_compat'
                                            -- | 'gemini' | 'anthropic' | 'mistral'
                                            -- | 'cloudflare'
  preset        TEXT,                       -- key into providers/presets.toml
  base_url      TEXT NOT NULL,
  auth_ref      TEXT,                       -- pointer into `secret`; NULL = no auth
  extra         BLOB,                       -- e.g. cloudflare account_id, org headers
  enabled       INTEGER NOT NULL DEFAULT 1,
  is_local      INTEGER NOT NULL DEFAULT 0, -- drives UI ordering and cost display
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

-- Encrypted at rest (AES-GCM, key from VELOX_SECRET_KEY). Never returned by the API.
CREATE TABLE secret (
  ref        TEXT PRIMARY KEY,              -- 'provider:<id>:api_key'
  nonce      BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  hint       TEXT NOT NULL,                 -- masked form for the UI: 'sk-...4f2a'
  updated_at INTEGER NOT NULL
);

-- A user's saved sampling parameters per model, applied to every turn. Keyed by
-- model_ref rather than a provider foreign key: the provider may come from
-- configuration and have no row at all. Cached in-process on the completion path,
-- including the (common) absence of a row.
CREATE TABLE model_params (
  user_id    CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  model_ref  VARCHAR(255) NOT NULL,
  params     BLOB NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, model_ref)
);

-- Discovery cache. Truth lives in the backend; this survives restarts and lets the
-- model picker render before any health probe completes.
-- Not yet implemented: model listings and health are cached in memory for now.
CREATE TABLE model_cache (
  id             CHAR(26) PRIMARY KEY,
  provider_id    CHAR(26) NOT NULL REFERENCES provider(id) ON DELETE CASCADE,
  model_key      TEXT NOT NULL,             -- id as the backend names it
  display_name   TEXT,                      -- user rename
  hidden         INTEGER NOT NULL DEFAULT 0,
  capabilities   BLOB NOT NULL,             -- probed Capabilities struct
  price_in_ppm   INTEGER,                   -- micro-cents per 1M input tokens
  price_out_ppm  INTEGER,
  refreshed_at   INTEGER NOT NULL,
  UNIQUE (provider_id, model_key)
);

CREATE TABLE provider_health (
  provider_id  CHAR(26) PRIMARY KEY REFERENCES provider(id) ON DELETE CASCADE,
  state        TEXT NOT NULL,               -- 'up' | 'down' | 'degraded' | 'unknown'
  latency_ms   INTEGER,
  detail       TEXT,
  checked_at   INTEGER NOT NULL
);

-- User-defined model presets ("custom models" / personas).
CREATE TABLE custom_model (
  id             CHAR(26) PRIMARY KEY,
  owner_id       CHAR(26) REFERENCES app_user(id) ON DELETE CASCADE,
  slug           TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  description    TEXT,
  avatar_url     TEXT,
  system_prompt  TEXT,
  params         BLOB,                      -- sampling params overrides
  tools          BLOB,                      -- enabled tool/MCP ids
  knowledge_ids  BLOB,                      -- collection ids for RAG
  fallback_chain BLOB NOT NULL,             -- ordered [{provider_id, model_key}, ...]
  visibility     TEXT NOT NULL,             -- 'private' | 'shared' | 'public'
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);

CREATE TABLE prompt (
  id         CHAR(26) PRIMARY KEY,
  owner_id   CHAR(26) REFERENCES app_user(id) ON DELETE CASCADE,
  command    TEXT NOT NULL,                 -- '/summarize'
  title      TEXT NOT NULL,
  content    TEXT NOT NULL,                 -- may contain {{variables}}
  variables  BLOB,
  visibility TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (owner_id, command)
);
```

## Conversations

The message tree is the core structure: editing a message or regenerating an answer
creates a sibling, never mutates history.

```sql
CREATE TABLE folder (
  id         CHAR(26) PRIMARY KEY,
  user_id    CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  parent_id  CHAR(26) REFERENCES folder(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX ix_folder_user ON folder(user_id, parent_id);

CREATE TABLE chat (
  id             CHAR(26) PRIMARY KEY,
  user_id        CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  folder_id      CHAR(26) REFERENCES folder(id) ON DELETE SET NULL,
  title          TEXT NOT NULL,
  active_leaf_id CHAR(26),                  -- current branch tip; no FK (write order)
  model_ref      TEXT,                      -- last used 'provider_id:model_key'
  custom_model_id CHAR(26) REFERENCES custom_model(id) ON DELETE SET NULL,
  pinned         INTEGER NOT NULL DEFAULT 0,
  archived       INTEGER NOT NULL DEFAULT 0,
  message_count  INTEGER NOT NULL DEFAULT 0,
  meta           BLOB,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  deleted_at     INTEGER
);
-- The single index that makes the 10k-chat list a keyset scan:
CREATE INDEX ix_chat_list ON chat(user_id, archived, pinned DESC, updated_at DESC, id DESC)
  WHERE deleted_at IS NULL;          -- partial index (SQLite >= 3.8, Postgres)

CREATE TABLE message (
  id           CHAR(26) PRIMARY KEY,
  chat_id      CHAR(26) NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
  parent_id    CHAR(26),                    -- NULL for the root turn
  role         TEXT NOT NULL,               -- 'system'|'user'|'assistant'|'tool'
  content      TEXT NOT NULL DEFAULT '',    -- final text; written once, at stream end
  reasoning    TEXT,                        -- separated thinking, when the model emits it
  status       TEXT NOT NULL,               -- 'complete'|'streaming'|'stopped'|'error'
  model_ref    TEXT,                        -- what actually served it (after fallback)
  depth        INTEGER NOT NULL,            -- cached tree depth, for ordered fetch
  tokens_in    INTEGER,
  tokens_out   INTEGER,
  cost_micros  INTEGER,                     -- 0 for local models, NULL when unknown
  timings      BLOB,                        -- ttft_ms, tok_per_s, prompt_eval_*, eval_*
  error        BLOB,                        -- typed error payload when status='error'
  meta         BLOB,                        -- tool_calls, citations, attachments refs
  created_at   INTEGER NOT NULL
);
CREATE INDEX ix_message_chat ON message(chat_id, depth, id);
CREATE INDEX ix_message_parent ON message(chat_id, parent_id);
```

Loading a 5 000-message conversation is one indexed range scan of
`(chat_id, depth, id)` selecting only the columns the list needs; the tree is
reassembled in Python (a dict pass over ~5 000 rows, sub-millisecond) and the
client receives only the active path plus sibling counts. Bodies of off-path
branches are fetched on demand when the user switches branch.

```sql
CREATE TABLE attachment (
  id          CHAR(26) PRIMARY KEY,
  message_id  CHAR(26) NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  file_id     CHAR(26) NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,                -- 'image' | 'document' | 'audio'
  UNIQUE (message_id, file_id)
);

CREATE TABLE tag (
  id      CHAR(26) PRIMARY KEY,
  user_id CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  name    TEXT NOT NULL,
  color   TEXT,
  UNIQUE (user_id, name)
);
CREATE TABLE chat_tag (
  chat_id CHAR(26) NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
  tag_id  CHAR(26) NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  PRIMARY KEY (chat_id, tag_id)
);
CREATE INDEX ix_chat_tag_tag ON chat_tag(tag_id, chat_id);

CREATE TABLE share_link (
  id          CHAR(26) PRIMARY KEY,
  chat_id     CHAR(26) NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
  token       TEXT NOT NULL UNIQUE,
  snapshot_at INTEGER,                      -- NULL = live view, else frozen copy
  snapshot    BLOB,
  views       INTEGER NOT NULL DEFAULT 0,
  expires_at  INTEGER,
  created_at  INTEGER NOT NULL
);
```

### Full-text search

**Implemented in phase 6** (migration `9f1c6a2d7b4e`).

- SQLite: `message_fts` external-content FTS5 table over `message.content`, kept in
  sync by triggers, plus a `chat_fts` over `chat.title`. Both join back on the
  table's own implicit `rowid` (`content_rowid='rowid'`), not the ULID `id`, which is
  how an external-content FTS5 table bridges to a table keyed by a non-integer
  primary key.
- Postgres: a `tsv tsvector` column on `message` and `chat`, kept current by a
  `BEFORE INSERT OR UPDATE` trigger (not a generated column, so the same migration
  applies to already-populated tables without a full rewrite), with a GIN index.

The repository exposes one `SearchRepository.search(user_id, query, cursor)` method
(`db/repositories/search.py`); each dialect's query is a separate prepared statement
selected by `Database.is_sqlite`, merged and re-paginated in Python. Results are
ordered by recency (`created_at` descending, then id) rather than by relevance score:
SQLite's `bm25()` and PostgreSQL's `ts_rank()` are not comparable, and a shared,
dialect-neutral ordering key is what makes the keyset cursor work identically on both
— a deliberate trade-off, not an oversight (see the phase 6 report for the reasoning).
Search covers both message content and chat titles; a hit's `kind` field tells the
client which.

## Files and RAG

```sql
CREATE TABLE file (
  id          CHAR(26) PRIMARY KEY,
  user_id     CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  filename    TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes  INTEGER NOT NULL,
  sha256      TEXT NOT NULL,                -- dedup key
  storage_key TEXT NOT NULL,                -- path under the data dir, or object key
  created_at  INTEGER NOT NULL
);
CREATE INDEX ix_file_user_hash ON file(user_id, sha256);

CREATE TABLE collection (
  id          CHAR(26) PRIMARY KEY,
  owner_id    CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  description TEXT,
  embedder_ref TEXT NOT NULL,               -- 'fastembed:bge-small-en-v1.5' or provider
  dim         INTEGER NOT NULL,
  chunking    BLOB NOT NULL,
  visibility  TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE document (
  id            CHAR(26) PRIMARY KEY,
  collection_id CHAR(26) NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
  file_id       CHAR(26) REFERENCES file(id) ON DELETE SET NULL,
  source_url    TEXT,                       -- web-search results have no file
  title         TEXT NOT NULL,
  status        TEXT NOT NULL,              -- 'pending'|'parsing'|'embedding'|'ready'|'failed'
  progress      INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  chunk_count   INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE INDEX ix_document_collection ON document(collection_id, status);

CREATE TABLE chunk (
  id           CHAR(26) PRIMARY KEY,
  document_id  CHAR(26) NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  content      TEXT NOT NULL,
  token_count  INTEGER NOT NULL,
  locator      BLOB,                        -- page/line span for clickable citations
  UNIQUE (document_id, ordinal)
);

-- One physical vector table per collection, named `chunk_vec_<collection_id>`
-- (rag/store/base.py:vector_table_name), because sqlite-vec's vec0 and pgvector's
-- vector(n) are both fixed-width per table and different collections may use
-- embedders of different dimensions. Not versioned in alembic migrations for the
-- same reason: the width isn't known until a collection is created.
-- SQLite (default, ADR-0011): `CREATE VIRTUAL TABLE chunk_vec_<id> USING vec0(
--   chunk_id TEXT PRIMARY KEY, embedding float[<dim>] distance_metric=cosine)`,
-- via the sqlite-vec loadable extension (rag/store/sqlite_vec.py).
-- PostgreSQL: `chunk_vec_<id>(chunk_id TEXT PRIMARY KEY, embedding vector(<dim>))`
-- with an HNSW index (`vector_cosine_ops`), via the pgvector extension
-- (rag/store/pgvector.py). Both are created by `CollectionRepository`'s caller
-- (api/routes/rag.py) at collection-creation time, not by a migration.
```

## Tools, MCP, jobs, usage

```sql
CREATE TABLE mcp_server (
  id         CHAR(26) PRIMARY KEY,
  owner_id   CHAR(26) REFERENCES app_user(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  transport  TEXT NOT NULL,                 -- 'stdio' | 'http_sse'
  config     BLOB NOT NULL,                 -- command/args/env or url/headers
  auth_ref   TEXT,                          -- secrets go to `secret`, never here
  enabled    INTEGER NOT NULL DEFAULT 1,
  approval   TEXT NOT NULL,                 -- 'always'|'once'|'never'
  tool_cache BLOB,
  created_at INTEGER NOT NULL
);

-- As implemented (phase 8): `owner_id` NULL means an instance-wide server, visible to
-- every signed-in user, not just its creator — the same convention `provider` uses
-- implicitly (there is no per-user provider). `config` never carries a credential;
-- `auth_ref`, when set, is the row's own id in `secret` (ADR-0013), the same
-- one-secret-per-row convention `provider.auth_ref` uses. `tool_cache` is a JSON array
-- of `{name, description, input_schema}` objects, written by `POST
-- /api/mcp/servers/{id}/connect` (mcp/manager.py) and read on the completion path
-- without ever reconnecting to the server mid-turn. No connect/call operation gets a
-- `job` row or an in-memory job handle: both are synchronous RPCs from the caller's
-- point of view (a subprocess spawn or one HTTP round trip), not a long-running,
-- progress-reporting background task like a model pull or a RAG ingest.

-- No `job` table exists. ADR-0017 (model pulls) found no recoverable state worth a
-- table: a download or an ingest is idempotent and resumable from what is already on
-- disk, so persisting "a job was running" bought nothing a restart could not recompute.
-- `services/model_jobs.py` and `services/rag_jobs.py` (ADR-0019) hold jobs in memory
-- instead, as a snapshot-plus-version-counter per job, kinds `pull`/`create` and
-- `ingest`/`embed` respectively. RAG ingest's durable state is the `document` row's
-- own `status`/`progress`/`error`/`chunk_count` columns, updated as the job runs.

-- One row per completed turn. Feeds quotas, cost reports and the admin dashboard.
-- Never read on the hot path; written by the persistence queue.
CREATE TABLE usage_event (
  id          CHAR(26) PRIMARY KEY,
  user_id     CHAR(26) NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  provider_id CHAR(26),
  model_key   TEXT NOT NULL,
  message_id  CHAR(26),
  tokens_in   INTEGER NOT NULL DEFAULT 0,
  tokens_out  INTEGER NOT NULL DEFAULT 0,
  cost_micros INTEGER NOT NULL DEFAULT 0,
  ttft_ms     INTEGER,
  duration_ms INTEGER,
  outcome     TEXT NOT NULL,                -- 'ok'|'stopped'|'error'
  created_at  INTEGER NOT NULL
);
CREATE INDEX ix_usage_user_time ON usage_event(user_id, created_at DESC);

CREATE TABLE setting (
  key        TEXT PRIMARY KEY,
  value      BLOB NOT NULL,
  updated_at INTEGER NOT NULL
);
```

## SQLite pragmas (set on every connection)

`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`,
`foreign_keys=ON`, `temp_store=MEMORY`, `mmap_size=268435456`,
`cache_size=-32000`. Writes go through a single writer connection; reads use a small
pool, so WAL readers never block on the writer.
