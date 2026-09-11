"""Time helpers.

Two clocks are used throughout the codebase and they must never be confused:

* Wall-clock milliseconds since the Unix epoch, UTC, stored in the database.
* A monotonic millisecond counter, used for latency accounting (time-to-first-token,
  stream duration) where wall-clock jumps would corrupt the measurement.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

__all__ = ["MonotonicTimer", "from_ms", "monotonic_ms", "now_ms", "to_ms"]


def now_ms() -> int:
    """Return the current UTC time as integer milliseconds since the Unix epoch."""
    return time.time_ns() // 1_000_000


def monotonic_ms() -> float:
    """Return a monotonic millisecond counter, unaffected by wall-clock changes."""
    return time.perf_counter() * 1_000.0


def to_ms(moment: datetime) -> int:
    """Convert an aware datetime to epoch milliseconds.

    Args:
        moment: A timezone-aware datetime. Naive datetimes are rejected, because
            silently assuming a timezone is how timestamps end up wrong.

    Returns:
        Milliseconds since the Unix epoch.

    Raises:
        ValueError: If ``moment`` is naive.
    """
    if moment.tzinfo is None:
        raise ValueError("naive datetime cannot be converted to epoch milliseconds")
    return int(moment.timestamp() * 1_000)


def from_ms(milliseconds: int) -> datetime:
    """Convert epoch milliseconds to an aware UTC datetime."""
    return datetime.fromtimestamp(milliseconds / 1_000, tz=UTC)


class MonotonicTimer:
    """Measure elapsed milliseconds from a fixed monotonic start point.

    Used on the streaming path to record time-to-first-token and total stream
    duration without allocating anything per token.

    Example:
        >>> timer = MonotonicTimer()
        >>> ttft = timer.elapsed_ms()  # at the first token
    """

    __slots__ = ("_start",)

    def __init__(self) -> None:
        """Start the timer."""
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        """Return milliseconds elapsed since the timer was created."""
        return (time.perf_counter() - self._start) * 1_000.0

    def reset(self) -> None:
        """Restart the timer from now."""
        self._start = time.perf_counter()
