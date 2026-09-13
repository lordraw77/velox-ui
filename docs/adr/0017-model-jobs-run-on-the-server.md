# ADR-0017: Model downloads run as server-side jobs

**Status:** accepted
**Date:** 2026-09-13

## Context
A model download can take an hour on a home connection, and creating a model with
re-quantisation can run for minutes without a byte of progress. The design first
described `POST /api/providers/{id}/local/pull` as a plain server-sent-events response:
the download lives exactly as long as the request.

That couples the download to a browser tab. Closing the tab, reloading the page or a
laptop going to sleep cancels the upstream request, and Ollama stops downloading. A
second tab has no way to see the progress and would start a second pull of the same
weights. A slow client reading the stream would also slow the loop that reads Ollama's
progress, because both sit in the same coroutine.

## Decision
A pull or a create is a job owned by the server process (`services/model_jobs.py`).
Starting one returns immediately — with the progress stream, or with `?detach=true`
just the job's snapshot — and any number of subscribers follow it through
`GET /api/model-jobs/{id}/events`. Starting a job for a model that already has one
running joins it instead.

Progress is a **snapshot with a version counter**, not a queue of events. A subscriber
waits for the version to move, reads the latest state, and is throttled to four frames
a second; whatever happened in between is folded into the next snapshot. The job never
waits for a subscriber, and a subscriber that falls behind skips ahead instead of
replaying history.

Per-layer progress from Ollama is aggregated into one byte count, so the percentage
describes the whole model. Finished jobs stay listed for fifteen minutes (at most fifty),
so a page reloaded after completion still shows the outcome. `DELETE /api/model-jobs/{id}`
cancels; Ollama keeps the partial download and resumes it on the next pull.

## Consequences
- Closing the interface never cancels a download; only an explicit cancel or a server
  shutdown does.
- Jobs are in memory. A restart forgets them (Ollama's own partial download survives),
  which is acceptable for an operation that is idempotent and resumable; persisting them
  would add a table for no recoverable state.
- The same mechanism will carry RAG ingestion progress in phase 7, where the brief
  already requires ingestion to run in the background with visible status.
- With several worker processes, a job is visible only from the worker that started it.
  The default is one worker (ADR-0002); this is noted as a reason to keep it that way.
