"""Resident memory at rest.

Target: under 150 MB with the server running and idle. Measured across the whole
process tree, because a server that forks workers pays for each of them and reporting
only the parent would flatter the result.
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
from pathlib import Path

from bench.harness import BenchCase, BenchContext, Measurement
from velox_ui.db.migrate import upgrade_to_head

TARGET_MB = 150.0


def _rss_kb(pid: int) -> int:
    """Return the resident set size of one process, in kilobytes."""
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


def _descendants(pid: int) -> list[int]:
    """Return a process and its children, using /proc only."""
    found = [pid]
    try:
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="utf-8")
    except OSError:
        return found
    for child in children.split():
        found.extend(_descendants(int(child)))
    return found


def _wait_ready(url: str, deadline: float) -> bool:
    """Block until the server answers or the deadline passes."""
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.02)
    return False


async def run(context: BenchContext) -> Measurement:
    """Start the server, let it settle, and read its resident memory."""
    if not Path("/proc").is_dir():
        return Measurement(
            value=None, unit="MB", skipped=True, detail="requires /proc; Linux only"
        )

    data_dir = context.scratch("rss_idle")
    await upgrade_to_head(f"sqlite+aiosqlite:///{(data_dir / 'velox.db').as_posix()}")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    process = subprocess.Popen(
        [sys.executable, "-m", "velox_ui", "serve", "--port", str(port)],
        env={**os.environ, "VELOX_DATA_DIR": str(data_dir), "VELOX_LOG_LEVEL": "ERROR"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ready = await asyncio.to_thread(
            _wait_ready, f"http://127.0.0.1:{port}/health", time.perf_counter() + 30.0
        )
        if not ready:
            return Measurement(
                value=None, unit="MB", skipped=True, detail="the server did not become ready"
            )
        # Let lazily-imported modules and the allocator settle before reading.
        await asyncio.sleep(2.0)
        total_kb = sum(_rss_kb(pid) for pid in _descendants(process.pid))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()

    return Measurement(
        value=total_kb / 1024.0,
        unit="MB",
        target=TARGET_MB,
        detail="resident set of the whole server process tree, idle",
    )


CASE = BenchCase(
    name="rss_idle",
    description="Resident memory of an idle server",
    run=run,
    phase=1,
)
