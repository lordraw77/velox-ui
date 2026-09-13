"""Long-running model operations: downloads and model creation.

A model download can take an hour. It therefore runs as a server-side job rather than
inside the request that started it (ADR-0017):

* **Closing the tab does not cancel it.** The request that starts a pull only
  subscribes to its progress; the download belongs to the server.
* **Anyone can watch it.** A reloaded page, a second tab or another administrator lists
  running jobs and subscribes to the same progress, instead of starting a second
  download of the same model.
* **A slow subscriber cannot slow it down.** Progress is a snapshot, not a queue of
  events: a subscriber that falls behind skips straight to the latest state, and the
  job never waits for anyone.

Ollama reports progress per layer (one digest per blob); the snapshot aggregates layers
into one byte count so a percentage means the whole model, not whichever blob happens
to be downloading.
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
from velox_ui.providers.base import PullProgress
from velox_ui.state import AppState

__all__ = ["JobSnapshot", "ModelJob", "ModelJobs"]

_log = logging.getLogger("velox.model_jobs")

JOB_RETENTION_S = 15 * 60
"""How long a finished job stays listed, so a page reloaded afterwards shows the outcome."""

MAX_FINISHED = 50
PROGRESS_INTERVAL_S = 0.25
"""Minimum spacing of progress frames to one subscriber. Four a second reads as live."""

HEARTBEAT_S = 15.0
_SPEED_WINDOW_S = 1.0

type JobKind = Literal["pull", "create"]
type JobState = Literal["running", "succeeded", "failed", "cancelled"]


class JobSnapshot(msgspec.Struct, frozen=True):
    """The state of a job at one moment, as sent to the interface.

    Attributes:
        id: Job id.
        kind: ``pull`` or ``create``.
        provider_id: The backend doing the work.
        model: The model being downloaded or created.
        state: ``running``, ``succeeded``, ``failed`` or ``cancelled``.
        status: The backend's own description of the current step.
        completed_bytes: Bytes done across every layer, when the step has a size.
        total_bytes: Bytes in total across every layer seen so far.
        bytes_per_second: Recent transfer rate.
        started_at: Epoch milliseconds.
        finished_at: Epoch milliseconds, once finished.
        error: The typed error payload, when it failed.
    """

    id: str
    kind: str
    provider_id: str
    model: str
    state: str
    status: str
    completed_bytes: int | None
    total_bytes: int | None
    bytes_per_second: float | None
    started_at: int
    finished_at: int | None
    error: dict[str, Any] | None


class ModelJob:
    """One running or finished operation, and its subscribers' wake-up signal."""

    def __init__(self, *, kind: JobKind, provider_id: str, model: str) -> None:
        """Create a job in the running state."""
        self.id = new_ulid()
        self.kind = kind
        self.provider_id = provider_id
        self.model = model
        self.state: JobState = "running"
        self.status = "starting"
        self.started_at = now_ms()
        self.finished_at: int | None = None
        self.finished_monotonic: float | None = None
        self.error: dict[str, Any] | None = None
        self.task: asyncio.Task[None] | None = None
        self.version = 0
        self._changed = asyncio.Event()
        self._layers: dict[str, tuple[int, int]] = {}
        self._speed: float | None = None
        self._speed_mark: tuple[float, int] | None = None

    @property
    def finished(self) -> bool:
        """Whether the job has stopped, for any reason."""
        return self.state != "running"

    def apply(self, progress: PullProgress) -> None:
        """Fold one progress line into the job."""
        self.status = progress.status or self.status
        if progress.digest and progress.total_bytes:
            self._layers[progress.digest] = (
                progress.completed_bytes or 0,
                progress.total_bytes,
            )
            self._update_speed()
        self._bump()

    def finish(self, state: JobState, *, error: dict[str, Any] | None = None) -> None:
        """Mark the job finished and wake every subscriber."""
        self.state = state
        self.error = error
        self.finished_at = now_ms()
        self.finished_monotonic = time.monotonic()
        if state == "succeeded":
            self.status = "success"
        self._bump()

    def snapshot(self) -> JobSnapshot:
        """The current state."""
        completed = sum(done for done, _ in self._layers.values()) if self._layers else None
        total = sum(size for _, size in self._layers.values()) if self._layers else None
        return JobSnapshot(
            id=self.id,
            kind=self.kind,
            provider_id=self.provider_id,
            model=self.model,
            state=self.state,
            status=self.status,
            completed_bytes=completed,
            total_bytes=total,
            bytes_per_second=round(self._speed, 1) if self._speed is not None else None,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
        )

    async def wait_for_change(self, seen: int, timeout_s: float) -> bool:
        """Wait until the job has moved past version ``seen``.

        Returns:
            ``True`` if it changed, ``False`` if the timeout elapsed first.
        """
        if self.version != seen:
            return True
        event = self._changed
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True

    def _bump(self) -> None:
        """Advance the version and wake everyone waiting on the previous one."""
        self.version += 1
        event, self._changed = self._changed, asyncio.Event()
        event.set()

    def _update_speed(self) -> None:
        """Maintain a smoothed transfer rate over roughly one-second windows."""
        now = time.monotonic()
        done = sum(value for value, _ in self._layers.values())
        if self._speed_mark is None:
            self._speed_mark = (now, done)
            return
        mark_time, mark_done = self._speed_mark
        elapsed = now - mark_time
        if elapsed < _SPEED_WINDOW_S:
            return
        sample = max(0.0, (done - mark_done) / elapsed)
        self._speed = sample if self._speed is None else 0.6 * sample + 0.4 * self._speed
        self._speed_mark = (now, done)


class ModelJobs:
    """Every model job this process knows about.

    Args:
        state: Application state, for spawning tasks and invalidating model caches.
    """

    def __init__(self, state: AppState) -> None:
        """Start with no jobs."""
        self._state = state
        self._jobs: dict[str, ModelJob] = {}

    def start(
        self,
        *,
        kind: JobKind,
        provider_id: str,
        model: str,
        operation: Callable[[], AsyncIterator[PullProgress]],
    ) -> ModelJob:
        """Start a job, or return the one already running for the same model.

        Joining rather than starting a second job matters for pulls: two downloads of
        the same weights to the same host would at best share a cache and at worst
        fight over it.

        Args:
            kind: ``pull`` or ``create``.
            provider_id: The backend.
            model: The model name.
            operation: Called once to obtain the progress iterator.
        """
        self._prune()
        for job in self._jobs.values():
            if (
                not job.finished
                and job.kind == kind
                and job.provider_id == provider_id
                and job.model == model
            ):
                return job
        job = ModelJob(kind=kind, provider_id=provider_id, model=model)
        self._jobs[job.id] = job
        job.task = self._state.spawn(self._run(job, operation))
        _log.info(
            "model job started",
            extra={"job": job.id, "kind": kind, "provider": provider_id, "model": model},
        )
        return job

    def get(self, job_id: str) -> ModelJob | None:
        """One job, running or recently finished."""
        return self._jobs.get(job_id)

    def list(self) -> list[JobSnapshot]:
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
        self, job: ModelJob, operation: Callable[[], AsyncIterator[PullProgress]]
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
            _log.warning(
                "model job failed",
                extra={"job": job.id, "code": str(exc.code), "detail": exc.message},
            )
        except Exception as exc:
            job.finish(
                "failed",
                error={
                    "code": "internal",
                    "message": "The operation failed unexpectedly.",
                    "retryable": False,
                },
            )
            _log.error("model job crashed", extra={"job": job.id}, exc_info=exc)
        else:
            job.finish("succeeded")
            _log.info("model job finished", extra={"job": job.id})
        finally:
            # Whatever happened, the backend's model list may have changed.
            self._state.providers.invalidate(job.provider_id)

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


async def job_events(job: ModelJob) -> AsyncIterator[bytes]:
    """Stream a job's progress as server-sent events until it finishes.

    Frames: ``progress`` carrying a :class:`JobSnapshot`, repeated as the job advances
    and at most every :data:`PROGRESS_INTERVAL_S`; then ``done`` with the final
    snapshot. A heartbeat comment keeps a proxy from closing a quiet stream while a
    backend resolves a manifest.

    Closing the stream unsubscribes and nothing else: the job continues.
    """
    seen = -1
    while True:
        if job.version != seen:
            seen = job.version
            snapshot = job.snapshot()
            if job.finished:
                yield encode_event("done", msgspec.to_builtins(snapshot))
                return
            yield encode_event("progress", msgspec.to_builtins(snapshot))
            # Throttle: whatever arrives in the meantime is folded into the next snapshot.
            with contextlib.suppress(TimeoutError):
                await asyncio.sleep(PROGRESS_INTERVAL_S)
            continue
        if not await job.wait_for_change(seen, HEARTBEAT_S):
            yield HEARTBEAT
