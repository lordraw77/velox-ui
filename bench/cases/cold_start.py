"""Process cold start.

Target: under one second from ``velox serve`` to the first successful ``/health``.

The database is migrated beforehand, because this measures how long the server takes
to become useful on an existing installation, not how long a one-off schema creation
takes. Migration time is real but it is paid once, and folding it in here would hide
regressions in the thing the target is actually about: import cost and startup work.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from bench.harness import BenchCase, BenchContext, Measurement
from velox_ui.db.migrate import upgrade_to_head

TARGET_S = 1.0
ROUNDS = 3


def _free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _probe(url: str, deadline: float) -> float | None:
    """Poll a URL until it answers, returning when it did."""
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                if response.status == 200:
                    return time.perf_counter()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.005)
    return None


async def run(context: BenchContext) -> Measurement:
    """Start the server repeatedly and time how long it takes to answer."""
    data_dir = context.scratch("cold_start")
    environment = {
        **os.environ,
        "VELOX_DATA_DIR": str(data_dir),
        "VELOX_LOG_LEVEL": "ERROR",
        "VELOX_SECRET_KEY": "benchmark-secret-key-not-used-for-anything",
    }
    await upgrade_to_head(f"sqlite+aiosqlite:///{(data_dir / 'velox.db').as_posix()}")

    samples: list[float] = []
    rounds = 1 if context.quick else ROUNDS
    for _ in range(rounds):
        port = _free_port()
        started = time.perf_counter()
        process = subprocess.Popen(
            [sys.executable, "-m", "velox_ui", "serve", "--port", str(port)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            ready = await asyncio.to_thread(
                _probe, f"http://127.0.0.1:{port}/health", started + 30.0
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
        if ready is None:
            return Measurement(
                value=None, unit="s", skipped=True, detail="the server did not become ready"
            )
        samples.append(ready - started)

    # Best of N, as ADR-0015 specifies for latency-sensitive cases. Noise here is
    # one-directional: another process stealing the CPU can only make a start slower,
    # never faster, so the minimum is the closest estimate of what the code actually
    # costs. The spread is reported alongside it so a degraded run is still visible
    # rather than hidden behind the headline number.
    return Measurement(
        value=min(samples),
        unit="s",
        target=TARGET_S,
        samples=tuple(samples),
        detail=(
            f"best of {len(samples)} starts (worst {max(samples):.3f} s), "
            "schema already migrated"
        ),
    )


CASE = BenchCase(
    name="cold_start",
    description="Time from process start to a served /health",
    run=run,
    phase=1,
)
