# Benchmarks

Benchmarks are a CI gate, not a report (ADR-0015): `bench/` runs against
deterministic fake backends with a fixed token cadence, so a result needs no
GPU, no network and no API key, and is reproducible on a laptop or a CI
runner. Methodology, the case list, and how to add one are in
[`bench/README.md`](../bench/README.md); this page is the results side —
what the suite currently reports on the reference host, refreshed per
release, plus (once one exists) a comparative run against Open WebUI on the
same host and the same operations.

## Reproducing

```
python -m bench                 # every case, full sample count
python -m bench --quick         # fewer samples, for a local check
python -m bench --case ttft_overhead --case cold_start
```

## Current results

Measured on a 4-CPU Linux container (`Linux-5.14.0-737.el9.x86_64`, Python
3.12.13) — not dedicated hardware, so treat the absolute numbers as a floor
rather than a ceiling on a quieter machine.

Refreshed for 0.1.5.

| Case | Target | p50 | p95 | Status |
|---|---|---|---|---|
| `ttft_overhead` | < 15 ms p95 | 9.6 ms | 10.5 ms | pass |
| `cold_start` | < 1 s | 1.028 s | 1.109 s | **fails on this host** |
| `rss_idle` | < 150 MB | 131.3 MB | — | pass |
| `image_size` | < 250 MB | 189.2 MB | — | pass |
| `open_chat_5k` | < 150 ms | 10.1 ms | 13.2 ms | pass |
| `chat_list_10k` | < 30 ms | 1.3 ms | 1.8 ms | pass |
| `bundle_size` | < 200 KB gzip | 50.1 KB | — | pass |

Two figures moved since 0.1.0 and are worth naming rather than leaving to a
reader comparing tables. `ttft_overhead` gained about 0.4 ms at the median when
a turn stopped being its request (ADR-0024): the frames now pass through a
buffer the response reads, which is one scheduling hop per frame. `bundle_size`
grew from 46.2 KB to 50.1 KB gzipped across the same releases — MCP transport
selection, the turn notices and the upgrade guard — against a 200 KB budget.

`cold_start` runs marginally over budget on this particular host (a shared,
virtualized container, not the kind of machine the 1 s target was set
against) — verified against the immediately preceding commit as well, so
it is a pre-existing environment characteristic, not a regression from any
specific change. CI runs the full suite on its own runner on every PR
(`.github/workflows/ci.yml`); that is the number that gates a merge, not a
one-off local run.

`rss_idle` measures ~150.5 MB on the GitHub runner against the same 150 MB
target it meets at 131.3 MB here — glibc 2.39 on an Azure kernel versus glibc
2.34 on this host, with one worker either way (ADR-0015's gate had never
actually run in CI before 2026-09-16, because the job failed at its install
step). The CI job grants that one case 25 MB of headroom through
`VELOX_BENCH_HEADROOM_RSS_IDLE`, so the run reports it as `tolerated` rather
than `pass`: the target stays 150 MB, the slack stays visible, and any real
regression still fails the build. `bench/README.md` documents the mechanism.

`open_chat_5k`'s p95 covers the initial page; the harness separately reports
that reading the remaining ~4 940 messages back in 25 keyset pages takes
around 360 ms end-to-end, which is expected — it is 25 round trips, not one.

## Comparative run against Open WebUI

Not yet recorded. ADR-0015 calls for a same-host, same-operations comparison
refreshed per release; none has been run yet. `docs/migration-openwebui.md`
covers importing an existing Open WebUI installation's chats, which is the
first step toward such a comparison.
