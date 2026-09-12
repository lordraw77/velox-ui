"""Initial bundle size.

Target: under 200 KB gzip for what a browser must download before it can show a
conversation. Lazily loaded chunks — the markdown worker, each syntax grammar — are
reported but not counted: a conversation with no code in it never downloads a
highlighter, and counting those would penalise exactly the code-splitting that keeps
the first paint fast.

The case delegates to the frontend's own ``scripts/check-size.mjs`` rather than
reimplementing the measurement, so the number in CI and the number a frontend developer
sees from ``npm run size`` cannot drift apart.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from bench.harness import BenchCase, BenchContext, Measurement

TARGET_KB = 200.0

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "frontend" / "scripts" / "check-size.mjs"
_TOTAL = re.compile(r"^\s*total\s+([\d.]+)\s*KB", re.MULTILINE)
_DEFERRED = re.compile(r"lazily loaded, not counted: (\d+) chunks, ([\d.]+) KB")


async def run(context: BenchContext) -> Measurement:
    """Measure the gzipped size of the eagerly loaded chunks."""
    del context
    node = shutil.which("node")
    if node is None:
        return Measurement(value=None, unit="KB", skipped=True, detail="node not found")
    if not _SCRIPT.is_file():
        return Measurement(value=None, unit="KB", skipped=True, detail="frontend not present")
    if not (_REPO_ROOT / "src" / "velox_ui" / "web" / "assets").is_dir():
        return Measurement(
            value=None,
            unit="KB",
            skipped=True,
            detail="the frontend has not been built (npm --prefix frontend run build)",
        )

    process = await asyncio.create_subprocess_exec(
        node,
        str(_SCRIPT),
        cwd=str(_REPO_ROOT / "frontend"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", "replace")

    match = _TOTAL.search(output)
    if match is None:
        return Measurement(
            value=None,
            unit="KB",
            skipped=True,
            detail=f"could not parse output: {output[:160]}",
        )

    deferred = _DEFERRED.search(output)
    detail = "gzipped entry script and stylesheet"
    if deferred:
        detail += f"; {deferred.group(1)} lazy chunks ({deferred.group(2)} KB) not counted"

    return Measurement(
        value=float(match.group(1)),
        unit="KB",
        target=TARGET_KB,
        detail=detail,
    )


CASE = BenchCase(
    name="bundle_size",
    description="Gzipped size of the initial frontend bundle",
    run=run,
    phase=3,
)
