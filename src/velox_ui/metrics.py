"""Prometheus metrics.

The instrumentation is deliberately small and all of it is cheap: counters and
histograms only, no gauges computed by walking the database, and no label values
derived from user input. Labels carry the provider and the model, because "which model
is slow" and "which provider is burning the budget" are the two questions an operator
actually asks.

Bucket boundaries are chosen around the project's own targets. Time-to-first-token
overhead is bucketed in single milliseconds up to 25 ms so that the 15 ms p95 gate in
``bench/`` is visible in production data too, not just in the benchmark.
"""

from __future__ import annotations

from typing import Final

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST

__all__ = ["CONTENT_TYPE_LATEST", "METRICS", "Metrics", "render"]

_TTFT_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)
_OVERHEAD_BUCKETS: Final = (
    0.001,
    0.002,
    0.003,
    0.005,
    0.008,
    0.010,
    0.015,
    0.020,
    0.025,
    0.05,
    0.1,
    0.25,
)
_DURATION_BUCKETS: Final = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900)
_DB_BUCKETS: Final = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1)
_RATE_BUCKETS: Final = (0.5, 1, 2, 5, 10, 20, 40, 80, 160, 320, 640, 1280)


class Metrics:
    """Container for every metric velox-ui exports.

    Held as a single module-level instance so that metric objects are created exactly
    once. A dedicated registry is used instead of the global default, so tests can
    build a throwaway instance without duplicate-registration errors.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Create the metric family objects.

        Args:
            registry: Registry to attach to. A fresh one is created when omitted.
        """
        self.registry = registry if registry is not None else CollectorRegistry()

        self.http_requests = Counter(
            "velox_http_requests_total",
            "HTTP requests handled, by route template and status class.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "velox_http_request_duration_seconds",
            "Wall-clock duration of non-streaming HTTP requests.",
            ("method", "route"),
            buckets=(*_DB_BUCKETS, 2.5, 5, 10),
            registry=self.registry,
        )
        self.ttft = Histogram(
            "velox_completion_ttft_seconds",
            "Time from accepting a completion request to the first token reaching the client.",
            ("provider", "model"),
            buckets=_TTFT_BUCKETS,
            registry=self.registry,
        )
        self.ttft_overhead = Histogram(
            "velox_completion_ttft_overhead_seconds",
            "Time velox-ui itself adds before the first token, excluding backend latency.",
            ("provider",),
            buckets=_OVERHEAD_BUCKETS,
            registry=self.registry,
        )
        self.stream_duration = Histogram(
            "velox_completion_stream_duration_seconds",
            "Duration of a completion stream, from request to final token.",
            ("provider", "model"),
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.generation_rate = Histogram(
            "velox_completion_tokens_per_second",
            "Observed output token rate of a completed stream.",
            ("provider", "model"),
            buckets=_RATE_BUCKETS,
            registry=self.registry,
        )
        self.tokens = Counter(
            "velox_tokens_total",
            "Tokens consumed, by direction.",
            ("provider", "model", "direction"),
            registry=self.registry,
        )
        self.cost_micros = Counter(
            "velox_cost_micros_total",
            "Estimated spend in micro-cents. Always zero for local models.",
            ("provider", "model"),
            registry=self.registry,
        )
        self.completions = Counter(
            "velox_completions_total",
            "Completion turns, by outcome.",
            ("provider", "model", "outcome"),
            registry=self.registry,
        )
        self.provider_errors = Counter(
            "velox_provider_errors_total",
            "Provider failures, by typed error code.",
            ("provider", "code"),
            registry=self.registry,
        )
        self.db_query_duration = Histogram(
            "velox_db_query_duration_seconds",
            "Duration of instrumented repository queries.",
            ("operation",),
            buckets=_DB_BUCKETS,
            registry=self.registry,
        )
        self.background_writes = Counter(
            "velox_background_writes_total",
            "Rows written by the off-path persistence queue, by kind.",
            ("kind",),
            registry=self.registry,
        )


METRICS = Metrics()
"""The process-wide metrics instance."""


def render() -> bytes:
    """Render the current metric values in Prometheus exposition format."""
    return generate_latest(METRICS.registry)
