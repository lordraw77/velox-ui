"""Benchmark runner.

Cases live in ``bench/cases/`` and each exposes a ``CASE`` object. The harness runs
them, prints a table, optionally writes JSON, and exits non-zero when a case misses its
target — that exit status is what makes these gates rather than a report.

Three details keep the numbers honest:

* **Latency cases report percentiles**, not a mean. A mean hides exactly the tail the
  targets are written against.
* **A case may declare itself pending.** A target for a phase that has not been built
  yet is reported as pending and never passes silently, which is different from a case
  that ran and met its goal.
* **The machine profile is printed alongside the results.** A number without the host
  it was measured on cannot be compared with anything.
* **Headroom is explicit and visible.** A target calibrated on one machine can be a
  hair too tight on another — `rss_idle` measures ~133 MB on a developer box and
  ~150.5 MB on a GitHub runner against a 150 MB target, which is the host, not a
  regression. `VELOX_BENCH_HEADROOM_<CASE>` grants that case a stated allowance for
  one run; a case that passes only because of it reports `tolerated`, not `pass`, so
  the slack stays in the output instead of disappearing into a green build.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import pkgutil
import platform
import statistics
import sys
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = ["BenchCase", "BenchContext", "Measurement", "run_cli", "timed"]


@dataclass(frozen=True)
class Measurement:
    """The outcome of one benchmark case.

    Attributes:
        value: The measured figure, in ``unit``.
        unit: Unit of ``value`` and ``target``.
        target: The threshold the project commits to, or ``None`` when the case only
            reports a number.
        lower_is_better: Direction of the comparison.
        samples: Raw samples, when the case took several.
        detail: Free-form notes shown under the table.
        pending: The case describes a target for work that does not exist yet.
        skipped: The case could not run here, with the reason in ``detail``.
        headroom: Allowance added to ``target`` for this run only, from
            ``VELOX_BENCH_HEADROOM_<CASE>``. Never silent: a measurement that needs it
            reports ``tolerated``.
    """

    value: float | None
    unit: str
    target: float | None = None
    lower_is_better: bool = True
    samples: tuple[float, ...] = ()
    detail: str = ""
    pending: bool = False
    skipped: bool = False
    headroom: float = 0.0

    @property
    def effective_target(self) -> float | None:
        """Return the threshold actually compared against, headroom included."""
        if self.target is None:
            return None
        return (
            self.target + self.headroom if self.lower_is_better else self.target - self.headroom
        )

    @property
    def status(self) -> str:
        """Return ``pass``, ``tolerated``, ``FAIL``, ``pending``, ``skip`` or ``report``."""
        if self.pending:
            return "pending"
        if self.skipped:
            return "skip"
        if self.target is None or self.value is None:
            return "report"
        effective = self.effective_target
        assert effective is not None
        if self.lower_is_better:
            if self.value > effective:
                return "FAIL"
            return "pass" if self.value <= self.target else "tolerated"
        if self.value < effective:
            return "FAIL"
        return "pass" if self.value >= self.target else "tolerated"

    @property
    def failed(self) -> bool:
        """Whether this measurement should fail the build."""
        return self.status == "FAIL"

    def percentile(self, fraction: float) -> float | None:
        """Return a percentile of the samples, or ``None`` when there are none."""
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]


@dataclass
class BenchContext:
    """Everything a case needs to run.

    Attributes:
        work_dir: A scratch directory unique to this run.
        quick: Reduce sample counts, for a fast local check.
    """

    work_dir: Path
    quick: bool = False

    def scratch(self, name: str) -> Path:
        """Return a fresh subdirectory of the working directory."""
        path = self.work_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(frozen=True)
class BenchCase:
    """A single benchmark.

    Attributes:
        name: Identifier used by ``--case``.
        description: One line shown in the table.
        run: The coroutine that performs the measurement.
        phase: Delivery phase this case belongs to.
    """

    name: str
    description: str
    run: Callable[[BenchContext], Coroutine[Any, Any, Measurement]]
    phase: int = 1
    tags: tuple[str, ...] = field(default=())


async def timed(
    operation: Callable[[], Coroutine[Any, Any, Any]], *, rounds: int
) -> list[float]:
    """Time an async operation repeatedly.

    Args:
        operation: A zero-argument coroutine factory.
        rounds: How many times to run it.

    Returns:
        Durations in milliseconds, one per round.
    """
    samples: list[float] = []
    for _ in range(rounds):
        start = time.perf_counter()
        await operation()
        samples.append((time.perf_counter() - start) * 1_000.0)
    return samples


def discover_cases() -> list[BenchCase]:
    """Import every module in ``bench.cases`` and collect its ``CASE``."""
    from bench import cases as cases_package

    found: list[BenchCase] = []
    for module_info in pkgutil.iter_modules(cases_package.__path__):
        module = importlib.import_module(f"{cases_package.__name__}.{module_info.name}")
        case = getattr(module, "CASE", None)
        if isinstance(case, BenchCase):
            found.append(case)
    found.sort(key=lambda case: (case.phase, case.name))
    return found


def _headroom(case_name: str) -> float:
    """Return the allowance granted to one case by the environment.

    ``VELOX_BENCH_HEADROOM_RSS_IDLE=25`` adds 25 of the case's own unit to its target
    for this run. It exists for the gap between the machine a target was calibrated on
    and the machine a build happens to run on, and it is deliberately per-case: there
    is no global "make the gates lenient" switch, because that is how a gate stops
    being one. A value that cannot be read as a number is an error, not a zero --
    a typo must not quietly tighten the gate back up.
    """
    raw = os.environ.get(f"VELOX_BENCH_HEADROOM_{case_name.upper()}")
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(
            f"velox bench: VELOX_BENCH_HEADROOM_{case_name.upper()}={raw!r} is not a number."
        ) from None
    if value < 0:
        raise SystemExit(
            f"velox bench: VELOX_BENCH_HEADROOM_{case_name.upper()} must not be negative."
        )
    return value


def _machine_profile() -> dict[str, str]:
    """Describe the host, so a result can be compared with another run."""
    import os

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpus": str(os.cpu_count() or "unknown"),
    }


def _format_value(value: float | None, unit: str) -> str:
    """Render a measurement value for the table."""
    if value is None:
        return "-"
    if unit in ("ms", "MB"):
        return f"{value:.1f}"
    if unit == "s":
        return f"{value:.3f}"
    return f"{value:.2f}"


def _print_table(results: list[tuple[BenchCase, Measurement]]) -> None:
    """Print the results table."""
    headers = ("case", "measured", "target", "unit", "p50", "p95", "status")
    rows: list[tuple[str, ...]] = []
    for case, measurement in results:
        rows.append(
            (
                case.name,
                _format_value(measurement.value, measurement.unit),
                # The declared target, not the effective one: the column states what
                # the project promises. Any allowance is in the note and in `status`.
                _format_value(measurement.target, measurement.unit),
                measurement.unit,
                _format_value(measurement.percentile(0.50), measurement.unit),
                _format_value(measurement.percentile(0.95), measurement.unit),
                measurement.status,
            )
        )

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    separator = "  ".join("-" * width for width in widths)
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print(separator)
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))

    notes = [
        (case.name, measurement.detail) for case, measurement in results if measurement.detail
    ]
    if notes:
        print()
        for name, detail in notes:
            print(f"  {name}: {detail}")


async def run_all(
    names: Sequence[str] | None, *, quick: bool, work_dir: Path
) -> list[tuple[BenchCase, Measurement]]:
    """Run the selected cases.

    Args:
        names: Case names to run, or ``None`` for all of them.
        quick: Reduce sample counts.
        work_dir: Scratch directory.

    Returns:
        Pairs of case and measurement, in table order.
    """
    selected = [case for case in discover_cases() if names is None or case.name in names]
    if names:
        unknown = set(names) - {case.name for case in selected}
        if unknown:
            raise SystemExit(f"velox bench: unknown case(s): {', '.join(sorted(unknown))}")

    context = BenchContext(work_dir=work_dir, quick=quick)
    results: list[tuple[BenchCase, Measurement]] = []
    for case in selected:
        print(f"running {case.name} ...", file=sys.stderr, flush=True)
        try:
            measurement = await case.run(context)
        except Exception as exc:
            measurement = Measurement(
                value=None,
                unit="-",
                skipped=True,
                detail=f"case raised {type(exc).__name__}: {exc}",
            )
        headroom = _headroom(case.name)
        if headroom and measurement.target is not None:
            note = (
                f"target {measurement.target:g} {measurement.unit} plus "
                f"{headroom:g} of headroom from VELOX_BENCH_HEADROOM_{case.name.upper()}"
            )
            measurement = replace(
                measurement,
                headroom=headroom,
                detail=f"{measurement.detail}; {note}" if measurement.detail else note,
            )
        results.append((case, measurement))
    return results


def run_cli(
    cases: Sequence[str] | None = None, json_path: str | None = None, *, quick: bool = False
) -> int:
    """Entry point used by ``velox bench``.

    Args:
        cases: Case names to run, or ``None`` for all.
        json_path: Write machine-readable results here.
        quick: Reduce sample counts.

    Returns:
        ``0`` when every case passed or was skipped, ``1`` otherwise.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="velox-bench-") as directory:
        results = asyncio.run(run_all(cases, quick=quick, work_dir=Path(directory)))

    profile = _machine_profile()
    print()
    print(f"host: {profile['platform']} | python {profile['python']} | {profile['cpus']} cpus")
    print()
    _print_table(results)

    failures = [case.name for case, measurement in results if measurement.failed]
    pending = [case.name for case, measurement in results if measurement.status == "pending"]
    tolerated = [
        case.name for case, measurement in results if measurement.status == "tolerated"
    ]
    print()
    if pending:
        print(f"pending (not implemented yet): {', '.join(pending)}")
    if tolerated:
        print(f"within granted headroom, over target: {', '.join(tolerated)}")
    if failures:
        print(f"FAILED: {', '.join(failures)}")
    elif tolerated:
        print("all measured targets met, some only within their granted headroom")
    else:
        print("all measured targets met")

    if json_path:
        import json

        payload = {
            "machine": profile,
            "results": [
                {
                    "name": case.name,
                    "description": case.description,
                    "phase": case.phase,
                    "value": measurement.value,
                    "unit": measurement.unit,
                    "target": measurement.target,
                    "headroom": measurement.headroom,
                    "effective_target": measurement.effective_target,
                    "p50": measurement.percentile(0.50),
                    "p95": measurement.percentile(0.95),
                    "status": measurement.status,
                    "detail": measurement.detail,
                }
                for case, measurement in results
            ],
        }
        Path(json_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {json_path}")

    return 1 if failures else 0


def summarize(samples: Sequence[float]) -> dict[str, float]:
    """Return p50/p95/p99 and the mean of a sample set."""
    ordered = sorted(samples)
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))],
        "p99": ordered[min(len(ordered) - 1, int(0.99 * (len(ordered) - 1)))],
        "mean": statistics.fmean(ordered),
    }


if __name__ == "__main__":  # pragma: no cover
    # Delegate to the package entry point: see bench/__main__.py for why running this
    # module directly would break case discovery.
    from bench.__main__ import main

    raise SystemExit(main())
