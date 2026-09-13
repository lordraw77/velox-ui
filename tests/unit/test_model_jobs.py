"""Server-side model jobs: aggregation, joining, cancellation, and their event stream."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import msgspec
import pytest

from velox_ui.providers.base import PullProgress
from velox_ui.providers.errors import ModelNotFound
from velox_ui.services import model_jobs
from velox_ui.services.model_jobs import ModelJobs, job_events


class _Registry:
    def __init__(self) -> None:
        self.invalidated: list[str] = []

    def invalidate(self, provider_id: str) -> None:
        self.invalidated.append(provider_id)


class _State:
    """The two things a job manager needs from the application state."""

    def __init__(self) -> None:
        self.providers = _Registry()
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        return task


@pytest.fixture(autouse=True)
def _fast_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_jobs, "PROGRESS_INTERVAL_S", 0.0)


async def _layers() -> AsyncIterator[PullProgress]:
    yield PullProgress(status="pulling manifest")
    yield PullProgress(
        status="pulling a", digest="sha256:a", total_bytes=100, completed_bytes=40
    )
    yield PullProgress(
        status="pulling b", digest="sha256:b", total_bytes=300, completed_bytes=0
    )
    yield PullProgress(
        status="pulling a", digest="sha256:a", total_bytes=100, completed_bytes=100
    )
    yield PullProgress(
        status="pulling b", digest="sha256:b", total_bytes=300, completed_bytes=300
    )
    yield PullProgress(status="success")


async def _finish(job: model_jobs.ModelJob) -> None:
    assert job.task is not None
    with contextlib.suppress(asyncio.CancelledError):
        await job.task


async def test_progress_is_aggregated_across_layers() -> None:
    state = _State()
    jobs = ModelJobs(state)  # type: ignore[arg-type]
    job = jobs.start(kind="pull", provider_id="ollama-0", model="qwen3", operation=_layers)
    await _finish(job)

    snapshot = job.snapshot()
    assert snapshot.state == "succeeded"
    assert (snapshot.completed_bytes, snapshot.total_bytes) == (400, 400)
    assert state.providers.invalidated == ["ollama-0"], "the model list changed"


async def test_a_second_pull_of_the_same_model_joins_the_first() -> None:
    gate = asyncio.Event()

    async def blocked() -> AsyncIterator[PullProgress]:
        yield PullProgress(status="pulling manifest")
        await gate.wait()

    jobs = ModelJobs(_State())  # type: ignore[arg-type]
    first = jobs.start(kind="pull", provider_id="ollama-0", model="qwen3", operation=blocked)
    second = jobs.start(kind="pull", provider_id="ollama-0", model="qwen3", operation=blocked)
    other = jobs.start(kind="pull", provider_id="ollama-1", model="qwen3", operation=blocked)
    assert first is second, "two downloads of the same weights to one host"
    assert other is not first

    gate.set()
    await _finish(first)
    await _finish(other)


async def test_cancelling_stops_the_download() -> None:
    async def endless() -> AsyncIterator[PullProgress]:
        while True:
            yield PullProgress(status="pulling")
            await asyncio.sleep(0.01)

    jobs = ModelJobs(_State())  # type: ignore[arg-type]
    job = jobs.start(kind="pull", provider_id="ollama-0", model="big", operation=endless)
    await asyncio.sleep(0.03)
    assert jobs.cancel(job.id)
    await _finish(job)
    assert job.snapshot().state == "cancelled"
    assert not jobs.cancel(job.id), "a finished job cannot be cancelled again"


async def test_failures_carry_the_typed_error() -> None:
    async def failing() -> AsyncIterator[PullProgress]:
        yield PullProgress(status="pulling manifest")
        raise ModelNotFound("pull model manifest: file does not exist", provider_id="ollama-0")

    jobs = ModelJobs(_State())  # type: ignore[arg-type]
    job = jobs.start(kind="pull", provider_id="ollama-0", model="ghost", operation=failing)
    await _finish(job)
    snapshot = job.snapshot()
    assert snapshot.state == "failed"
    assert snapshot.error is not None and snapshot.error["code"] == "model_not_found"


async def test_events_end_with_the_final_state() -> None:
    jobs = ModelJobs(_State())  # type: ignore[arg-type]
    job = jobs.start(kind="pull", provider_id="ollama-0", model="qwen3", operation=_layers)
    frames = [frame async for frame in job_events(job)]

    last = frames[-1].decode()
    assert last.startswith("event: done\n")
    payload = msgspec.json.decode(last.split("data: ", 1)[1].strip())
    assert payload["state"] == "succeeded"
    assert all(frame.startswith(b"event: progress") for frame in frames[:-1])


async def test_a_late_subscriber_sees_the_outcome() -> None:
    jobs = ModelJobs(_State())  # type: ignore[arg-type]
    job = jobs.start(kind="pull", provider_id="ollama-0", model="qwen3", operation=_layers)
    await _finish(job)
    frames = [frame async for frame in job_events(job)]
    assert len(frames) == 1 and frames[0].startswith(b"event: done")
    assert [entry.id for entry in jobs.list()] == [job.id], "finished jobs stay listed"


async def test_a_job_does_not_wait_for_subscribers() -> None:
    # Progress is a snapshot rather than a queue, so nobody reading it cannot stall it.
    async def many() -> AsyncIterator[PullProgress]:
        for index in range(5_000):
            yield PullProgress(
                status="pulling", digest="sha256:a", total_bytes=5_000, completed_bytes=index
            )

    jobs = ModelJobs(_State())  # type: ignore[arg-type]
    job = jobs.start(kind="pull", provider_id="ollama-0", model="qwen3", operation=many)
    await asyncio.wait_for(_finish(job), timeout=5.0)
    assert job.snapshot().state == "succeeded"
