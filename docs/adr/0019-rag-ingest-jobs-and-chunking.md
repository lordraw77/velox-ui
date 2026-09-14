# ADR-0019: RAG ingest/embed as in-memory jobs; fixed-budget paragraph chunking

**Status:** accepted
**Date:** 2026-09-14

## Context
Phase 7 needed two decisions ADR-0010/0011/0014 leave open: how ingestion runs in the
background (the brief requires reusing "the existing job-queue mechanism" from
ADR-0017), and what chunking strategy `rag/chunking.py` implements (the brief asks for
"one solid default", not a multi-strategy system).

On jobs: `docs/design/02-db-schema.md` names a `job` table with kinds
`'ingest' | 'embed' | 'model_pull'`. It was never built. ADR-0017 implemented model
pulls as an in-memory, per-process job (`services/model_jobs.py`) instead, explicitly
because a pull is idempotent and resumable from what Ollama already has on disk —
persisting "a job is running" would add a table for no state a restart could not
recompute. That reasoning carries over unchanged to RAG ingestion: the source file is
already on disk (`file.storage_key`) and the `document` row already carries
`status`/`progress`/`error`/`chunk_count` as durable state, updated as the job runs. A
restart loses only the in-memory job handle, not any progress; re-POSTing the document
re-ingests from the same file.

## Decision
`services/rag_jobs.py` is a second instance of the ADR-0017 mechanism: the same
snapshot-plus-version-counter shape as `ModelJobs`/`ModelJob`, sized down to a percent
and a status string (no per-layer byte aggregation, since there are no layers).
`AppState.rag_jobs` mirrors `AppState.model_jobs`. Kinds are `ingest` and `embed`, per
the schema doc's naming, but this phase only ever produces `ingest` jobs — a job that
parses, chunks and embeds a document as one operation — because splitting "parse" and
"embed" into two separately-queued jobs would double the bookkeeping for no case this
phase needs; the `embed` kind is kept in the type so a future re-embed-only operation
(changing a collection's embedder) has a name to run under without a migration.

`GET /api/rag-jobs/{id}/events` follows the same route shape as
`GET /api/model-jobs/{id}/events`, not the single `GET /api/jobs/{id}` the API doc
sketched: there is no shared job registry across subsystems to serve a unified path
from, and building one now — merging two independently-typed in-memory registries
behind one endpoint — would be exactly the speculative abstraction the brief asks this
phase to avoid. `docs/design/02-db-schema.md` and `03-http-api.md` are updated to
describe what was actually built.

On chunking: paragraph-aware packing with a fixed token budget (400) and overlap (60),
approximating one token as four characters. No sentence splitter, no semantic
chunking, no per-file-type strategy: a paragraph is the unit that carries meaning in
plain text/markdown/PDF-extracted text alike, packing paragraphs up to a budget is
cheap and dependency-free, and the fixed overlap means a sentence split across a chunk
boundary is not made unretrievable by exactly one query landing on the boundary. A
paragraph longer than the budget on its own (a wall of text with no blank lines) is
hard-split on whitespace so no single chunk is unbounded.

## Consequences
- RAG jobs are lost on restart exactly like model-pull jobs (ADR-0017 accepts this
  trade for the same reason); a document mid-ingest at restart shows its last-written
  `status`/`progress` until re-POSTed.
- With more than one worker process, an ingest job is only visible from the worker
  that started it — the same limitation ADR-0017 already notes for `ModelJobs`, and the
  same reason (default is one worker, ADR-0002) to leave it.
- Chunking has no configurable strategy per file type. If PDF or code files later need
  different packing (page-aware, function-aware), that is a new function in
  `rag/chunking.py`, not a rewrite of this one.
- Retrieval is vector-only KNN (`rag/retrieve.py`); ADR-0011's hybrid BM25+vector
  fusion and reranking are not implemented in this phase — they are optional follow-ons
  named there, not required for a working `query`/citation path.
