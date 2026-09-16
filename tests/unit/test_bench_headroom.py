"""Benchmark gate arithmetic: targets, and the headroom the environment may grant.

The gates are only meaningful if slack is visible. A case that clears its target is a
`pass`; one that needs the allowance is `tolerated` and says so; one that exceeds even
the allowance still fails the build.
"""

from __future__ import annotations

import pytest
from bench.harness import Measurement, _headroom


def _rss(value: float, headroom: float = 0.0) -> Measurement:
    return Measurement(value=value, unit="MB", target=150.0, headroom=headroom)


def test_within_target_is_a_clean_pass() -> None:
    assert _rss(132.8).status == "pass"
    assert _rss(132.8, headroom=25.0).status == "pass"


def test_over_target_but_within_headroom_is_tolerated_not_passed() -> None:
    measurement = _rss(150.5, headroom=25.0)
    assert measurement.status == "tolerated"
    assert measurement.effective_target == 175.0
    # A tolerated case must not fail the build, or the allowance would be pointless.
    assert not measurement.failed


def test_over_target_without_headroom_fails() -> None:
    assert _rss(150.5).status == "FAIL"
    assert _rss(150.5).failed


def test_beyond_headroom_still_fails() -> None:
    assert _rss(200.0, headroom=25.0).status == "FAIL"


def test_headroom_applies_in_the_right_direction_when_higher_is_better() -> None:
    measurement = Measurement(
        value=95.0, unit="req/s", target=100.0, lower_is_better=False, headroom=10.0
    )
    assert measurement.effective_target == 90.0
    assert measurement.status == "tolerated"
    assert (
        Measurement(
            value=85.0, unit="req/s", target=100.0, lower_is_better=False, headroom=10.0
        ).status
        == "FAIL"
    )


def test_headroom_is_read_per_case_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _headroom("rss_idle") == 0.0
    monkeypatch.setenv("VELOX_BENCH_HEADROOM_RSS_IDLE", "25")
    assert _headroom("rss_idle") == 25.0
    # Per case, deliberately: there is no global switch that loosens every gate.
    assert _headroom("cold_start") == 0.0


@pytest.mark.parametrize("raw", ["abc", "", "-5"])
def test_an_unusable_headroom_is_an_error_not_a_silent_zero(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("VELOX_BENCH_HEADROOM_RSS_IDLE", raw)
    with pytest.raises(SystemExit):
        _headroom("rss_idle")
