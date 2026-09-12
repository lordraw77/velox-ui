"""Overhead added before the first token.

Target: under 15 ms at p95. This is the project's central number and the one ADR-0004
exists to defend.

The measurement is a difference, not an absolute. Two requests are timed against the
same fake backend on the same loopback socket: one straight to the backend, one through
velox-ui. Whatever separates them is ours. Anything measured as an absolute would be
dominated by the backend's own latency and would say nothing about this server.

Both paths go over real sockets. An in-process ASGI transport would skip the HTTP
server, the middleware and the framing — most of what is being measured — and would
also buffer the response, hiding the very thing the number is about.

The fake backend is the one the contract tests use. Benchmarking against a different
fake than the one the adapters are verified against would measure a backend nobody
checked.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import msgspec

from bench.harness import BenchCase, BenchContext, Measurement

# The fakes live with the tests because that is what they are; the benchmark reuses
# them deliberately rather than keeping a second, divergent copy.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

TARGET_MS = 15.0
WARMUP = 5


async def run(context: BenchContext) -> Measurement:
    """Measure the difference between a direct call and a call through velox-ui."""
    from tests.fakes.ollama import create_app as create_ollama
    from tests.fakes.server import run_fake

    from velox_ui.app import create_app
    from velox_ui.settings import (
        AuthSettings,
        DatabaseSettings,
        ProviderSettings,
        Settings,
    )

    rounds = 20 if context.quick else 120
    data_dir = context.scratch("ttft")

    with run_fake(create_ollama()) as backend:
        settings = Settings(
            data_dir=data_dir,
            secret_key="benchmark-secret-key-not-used-for-anything",
            db=DatabaseSettings(
                url=f"sqlite+aiosqlite:///{(data_dir / 'bench.db').as_posix()}"
            ),
            auth=AuthSettings(),
            providers=ProviderSettings(ollama_hosts=(backend.base_url,), autodiscover=False),
            log_level="ERROR",
        )
        with run_fake(create_app(settings), lifespan="on") as app:
            direct, through = await _measure(app.base_url, backend.base_url, rounds=rounds)

    overhead = [t - d for t, d in zip(sorted(through), sorted(direct), strict=True)]
    ordered = sorted(overhead)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    direct_p50 = sorted(direct)[len(direct) // 2]
    through_p50 = sorted(through)[len(through) // 2]

    return Measurement(
        value=p95,
        unit="ms",
        target=TARGET_MS,
        samples=tuple(overhead),
        detail=(
            f"{rounds} rounds over loopback sockets; "
            f"direct p50 {direct_p50:.2f} ms, through velox-ui p50 {through_p50:.2f} ms"
        ),
    )


async def _measure(
    app_url: str, backend_url: str, *, rounds: int
) -> tuple[list[float], list[float]]:
    """Time first-token latency on both paths.

    Returns:
        ``(direct, through)`` sample lists, in milliseconds.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{app_url}/api/auth/register",
            json={
                "email": "bench@homelab.local",
                "password": "correct-horse-battery",
                "name": "Bench",
            },
        )
        response.raise_for_status()
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

        direct: list[float] = []
        through: list[float] = []

        for index in range(rounds + WARMUP):
            recording = index >= WARMUP

            # A fresh conversation per round. Reusing one would grow its history by two
            # messages each time, so later rounds would rebuild a longer context than
            # earlier ones and the measurement would drift upward for a reason that has
            # nothing to do with per-request overhead.
            chat_id = (
                await client.post(
                    f"{app_url}/api/chats", headers=headers, json={"title": "Bench"}
                )
            ).json()["id"]

            elapsed = await _time_direct(client, backend_url)
            if recording:
                direct.append(elapsed)

            elapsed = await _time_through(client, app_url, chat_id, headers)
            if recording:
                through.append(elapsed)

    return direct, through


async def _time_direct(client: httpx.AsyncClient, backend_url: str) -> float:
    """Time to the first token straight from the backend."""
    started = time.perf_counter()
    async with client.stream(
        "POST",
        f"{backend_url}/api/chat",
        json={
            "model": "llama3.2",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            chunk = msgspec.json.decode(line)
            if chunk.get("message", {}).get("content"):
                return (time.perf_counter() - started) * 1_000.0
    raise RuntimeError("the fake backend produced no token")


async def _time_through(
    client: httpx.AsyncClient, app_url: str, chat_id: str, headers: dict[str, str]
) -> float:
    """Time to the first token through velox-ui."""
    started = time.perf_counter()
    async with client.stream(
        "POST",
        f"{app_url}/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "hi", "model_ref": "ollama-0:llama3.2"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and '"t"' in line:
                return (time.perf_counter() - started) * 1_000.0
    raise RuntimeError("velox-ui produced no token")


CASE = BenchCase(
    name="ttft_overhead",
    description="Time-to-first-token overhead versus a direct backend call",
    run=run,
    phase=2,
)
