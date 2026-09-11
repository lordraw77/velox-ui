# ADR-0015: Benchmarks are a gate, not a report

**Status:** proposed
**Date:** 2026-09-11

## Context
The performance targets are stated as non-negotiable. Targets that are not measured
continuously erode.

## Decision
`bench/` runs against deterministic fake backends (so results are stable and need no GPU
or API key) and prints a table with p50/p95/p99. It runs in CI on every PR with
thresholds: TTFT overhead p95 < 15 ms, cold start < 1 s, idle RSS < 150 MB, image
< 250 MB, 5k-message open < 150 ms, 10k-chat list query < 30 ms. A regression fails the
build. `docs/benchmarks.md` additionally records comparative runs against Open WebUI on
the same host and the same operations, refreshed per release.

## Consequences
CI needs stable timing, so thresholds are measured against a fake backend with a fixed
token cadence and the harness reports the machine profile alongside the numbers. Noisy
runners are handled by taking the best of N runs for latency-sensitive cases and by
failing only on sustained regressions.
