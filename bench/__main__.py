"""Entry point for ``python -m bench``.

Running ``python -m bench.harness`` directly would load that module twice — once as
``__main__`` and once as ``bench.harness`` when a case imports it — so the ``BenchCase``
class checked by ``isinstance`` would not be the class the cases were built from, and
discovery would silently find nothing. Going through the package avoids the duplicate.
"""

from __future__ import annotations

import argparse

from bench.harness import run_cli


def main() -> int:
    """Parse arguments and run the suite."""
    parser = argparse.ArgumentParser(
        prog="python -m bench", description="Run the velox-ui benchmarks."
    )
    parser.add_argument("--case", action="append", help="Run only the named case. Repeatable.")
    parser.add_argument("--json", metavar="PATH", help="Write results as JSON to this path.")
    parser.add_argument(
        "--quick", action="store_true", help="Fewer samples, for a local check."
    )
    args = parser.parse_args()
    return run_cli(cases=args.case, json_path=args.json, quick=args.quick)


if __name__ == "__main__":
    raise SystemExit(main())
