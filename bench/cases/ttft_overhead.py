"""Overhead added before the first token.

Target: under 15 ms at p95, measured as the difference between calling a backend
directly and calling it through velox-ui. It is the project's central number and the
one ADR-0004 exists to defend.

It cannot be measured before the provider layer and the streaming endpoint exist, so
the case reports itself as pending rather than passing vacuously. It lands in phase 2,
together with a deterministic fake Ollama and llama.cpp server that emit tokens on a
fixed cadence, so the measurement isolates our overhead from the backend's latency.
"""

from __future__ import annotations

from bench.harness import BenchCase, BenchContext, Measurement

TARGET_MS = 15.0


async def run(context: BenchContext) -> Measurement:
    """Report the case as pending until the streaming path exists."""
    del context
    return Measurement(
        value=None,
        unit="ms",
        target=TARGET_MS,
        pending=True,
        detail="arrives in phase 2 with the provider layer and the fake backends",
    )


CASE = BenchCase(
    name="ttft_overhead",
    description="Time-to-first-token overhead versus a direct backend call",
    run=run,
    phase=2,
)
