"""Ingest and embed jobs: the same server-side job mechanism as model downloads.

ADR-0017 put model pulls and creations in a server-owned, in-memory job with a
snapshot-plus-version-counter progress model rather than a table, specifically because
persisting them "would add a table for no recoverable state" — the download is
resumable and idempotent on the backend side regardless of whether velox-ui remembers
starting it. The same reasoning applies to RAG ingestion: the ``document`` row already
carries ``status``/``progress`` as the durable, recoverable state (docs/design/02-db-schema.md),
so an ingest job restarted after a crash just re-ingests from the file that is still on
disk — there is nothing a separate ``job`` table would add. This module is therefore a
second instance of the ADR-0017 mechanism, not a new one: same snapshot/version/wake
shape as :mod:`velox_ui.services.model_jobs`, sized down (percent, not byte
aggregation) for the ``ingest``/``embed`` kinds named in docs/design/02-db-schema.md.
See ADR-0019.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

import msgspec

from velox_ui.api.sse import HEARTBEAT, encode_event
from velox_ui.clock import now_ms
from velox_ui.errors import VeloxError
from velox_ui.ids import new_ulid
from velox_ui.state import AppState

__all__ = ["RagJob", "RagJobSnapshot", "RagJobs", "RagProgress", "rag_job_events"]

_log = logging.getLogger("velox.rag_jobs")

JOB_RETENTION_S = 15 * 60
MAX_FINISHED = 50
PROGRESS_INTERVAL_S = 0.25
HEARTBEAT_S = 15.0

type RagJobKind = Literal["ingest", "embed"]
type RagJobState = Literal["running", "succeeded", "failed", "cancelled"]


class RagProgress(msgspec.Struct, frozen=True):
    """One step of an ingest/embed operation, yielded by ``services/rag_ingest.py``."""

    status: str
    progress: int  # 0-100


class RagJobSnapshot(msgspec.Struct, frozen=True):
    """The state of an ingest/embed job at one moment, as sent to the interface."""

    id: str
    kind: str
    collection_id: str
    document_id: str
    state: str
    status: str
    progress: int
    started_at: int
    finished_at: int | None
    error: dict[str, Any] | None


class RagJob:
    """One running or finished ingest/embed operation."""

    def __init__(self, *, kind: RagJobKind, collection_id: str, document_id: str) -> None:
        """Create a job in the running state."""
        self.id = new_ulid()
        self.kind = kind
        self.collection_id = collection_id
        self.document_id = document_id
        self.state: RagJobState = "running"
        self.status = "starting"
        self.progress = 0
        self.started_at = now_ms()
        self.finished_at: int | None = None
        self.finished_monotonic: float | None = None
        self.error: dict[str, Any] | None = None
        self.task: asyncio.Task[None] | None = None
        self.version = 0
        self._changed = asyncio.Event()

    @property
    def finished(self) -> bool:
        """Whether the job has stopped, for any reason."""
        return self.state != "running"

    def apply(self, progress: RagProgress) -> None:
        """Fold one progress step into the job."""
        self.status = progress.status
        self.progress = progress.progress
        self._bump()

    def finish(self, state: RagJobState, *, error: dict[str, Any] | None = None) -> None:
        """Mark the job finished and wake every subscriber."""
        self.state = state
        self.error = error
        self.finished_at = now_ms()
        self.finished_monotonic = time.monotonic()
        if state == "succeeded":
            self.status = "done"
            self.progress = 100
        self._bump()

    def snapshot(self) -> RagJobSnapshot:
        """The current state."""
        return RagJobSnapshot(
            id=self.id,
            kind=self.kind,
            collection_id=self.collection_id,
            document_id=self.document_id,
            state=self.state,
            status=self.status,
            progress=self.progress,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
        )

    async def wait_for_change(self, seen: int, timeout_s: float) -> bool:
        """Wait until the job has moved past version ``seen``."""
        if self.version != seen:
            return True
        event = self._changed
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True

    def _bump(self) -> None:
        self.version += 1
        event, self._changed = self._changed, asyncio.Event()
        event.set()


class RagJobs:
    """Every ingest/embed job this process knows about.

    Args:
        state: Application state, for spawning tasks.
    """

    def __init__(self, state: AppState) -> None:
        """Start with no jobs."""
        self._state = state
        self._jobs: dict[str, RagJob] = {}

    def start(
        self,
        *,
        kind: RagJobKind,
        collection_id: str,
        document_id: str,
        operation: Callable[[], AsyncIterator[RagProgress]],
    ) -> RagJob:
        """Start a job for one document.

        Ingesting the same document twice at once joins the job already running
        rather than racing it.
        """
        self._prune()
        for job in self._jobs.values():
            if not job.finished and job.document_id == document_id and job.kind == kind:
                return job
        job = RagJob(kind=kind, collection_id=collection_id, document_id=document_id)
        self._jobs[job.id] = job
        job.task = self._state.spawn(self._run(job, operation))
        _log.info(
            "rag job started",
            extra={"job": job.id, "kind": kind, "document": document_id},
        )
        return job

    def get(self, job_id: str) -> RagJob | None:
        """One job, running or recently finished."""
        return self._jobs.get(job_id)

    def for_document(self, document_id: str) -> RagJob | None:
        """The running job for a document, if any."""
        for job in self._jobs.values():
            if not job.finished and job.document_id == document_id:
                return job
        return None

    def list(self) -> list[RagJobSnapshot]:
        """Running jobs first, then recently finished ones, newest first."""
        self._prune()
        jobs = sorted(self._jobs.values(), key=lambda job: (job.finished, -job.started_at))
        return [job.snapshot() for job in jobs]

    def cancel(self, job_id: str) -> bool:
        """Cancel a running job. Returns whether there was one to cancel."""
        job = self._jobs.get(job_id)
        if job is None or job.finished or job.task is None:
            return False
        job.task.cancel()
        return True

    async def _run(
        self, job: RagJob, operation: Callable[[], AsyncIterator[RagProgress]]
    ) -> None:
        """Drive a job to completion, recording how it ended."""
        try:
            async for progress in operation():
                job.apply(progress)
        except asyncio.CancelledError:
            job.finish("cancelled")
            raise
        except VeloxError as exc:
            job.finish("failed", error=exc.to_payload()["error"])
            _log.warning("rag job failed", extra={"job": job.id, "code": str(exc.code)})
        except Exception as exc:
            job.finish(
                "failed",
                error={
                    "code": "internal",
                    "message": "Ingestion failed unexpectedly.",
                    "retryable": False,
                },
            )
            _log.error("rag job crashed", extra={"job": job.id}, exc_info=exc)
        else:
            job.finish("succeeded")
            _log.info("rag job finished", extra={"job": job.id})

    def _prune(self) -> None:
        """Forget finished jobs past their retention, keeping at most a bounded number."""
        now = time.monotonic()
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_monotonic is not None),
            key=lambda job: job.finished_monotonic or 0.0,
            reverse=True,
        )
        for index, job in enumerate(finished):
            expired = now - (job.finished_monotonic or now) > JOB_RETENTION_S
            if expired or index >= MAX_FINISHED:
                self._jobs.pop(job.id, None)


async def rag_job_events(job: RagJob) -> AsyncIterator[bytes]:
    """Stream a job's progress as server-sent events until it finishes."""
    seen = -1
    while True:
        if job.version != seen:
            seen = job.version
            snapshot = job.snapshot()
            if job.finished:
                yield encode_event("done", msgspec.to_builtins(snapshot))
                return
            yield encode_event("progress", msgspec.to_builtins(snapshot))
            with contextlib.suppress(TimeoutError):
                await asyncio.sleep(PROGRESS_INTERVAL_S)
            continue
        if not await job.wait_for_change(seen, HEARTBEAT_S):
            yield HEARTBEAT
