# Benchmarks

```bash
python -m bench                 # every case
python -m bench --quick         # fewer samples, for a local check
python -m bench --case cold_start --case rss_idle
python -m bench --json out.json
velox bench                     # the same thing through the CLI
```

The suite exits non-zero when a case misses its target. That exit status is the point:
these are gates, not a report (ADR-0015).

## Why the numbers are trustworthy

- **No external dependencies.** Every case runs against local fixtures or, from phase 2,
  deterministic fake backends that emit tokens on a fixed cadence. No GPU, no network,
  no API key, so a result is reproducible on a laptop and on a CI runner.
- **Percentiles, not means.** A mean hides exactly the tail the targets are written
  against.
- **The host is printed with the results.** A latency figure without the machine it was
  measured on cannot be compared with anything.
- **Pending is not passing.** A case whose subject has not been built yet reports
  `pending` and can never be mistaken for a green result.

## Cases

| Case | Target | Phase |
|---|---|---|
| `cold_start` | < 1 s from process start to a served `/health` | 1 |
| `rss_idle` | < 150 MB resident, idle | 1 |
| `image_size` | < 250 MB container image | 1 |
| `chat_list_10k` | < 30 ms per page over 10 000 conversations | 1 |
| `open_chat_5k` | < 150 ms to open a 5 000-message conversation | 1 |
| `ttft_overhead` | < 15 ms p95 added before the first token | 2 |

`cold_start` migrates the database beforehand, because it measures how long the server
takes to become useful on an existing installation — not how long a one-off schema
creation takes. `image_size` skips when the image has not been built locally; a missing
build tool is not a performance regression.

## Adding a case

Drop a module into `bench/cases/` exposing a `CASE = BenchCase(...)` whose `run`
returns a `Measurement`. Set `pending=True` while the subject does not exist yet, and
`skipped=True` with a reason when the environment cannot run it.
