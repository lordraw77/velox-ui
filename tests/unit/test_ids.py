"""ULID generation."""

from __future__ import annotations

import pytest

from velox_ui.ids import ULID_LENGTH, new_ulid, timestamp_of, ulid_at


def test_length_and_alphabet() -> None:
    value = new_ulid()
    assert len(value) == ULID_LENGTH
    assert set(value) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_ids_are_monotonic_within_a_millisecond() -> None:
    # Generating a burst guarantees collisions in the timestamp portion, which is
    # exactly the case that must still produce strictly increasing identifiers.
    values = [new_ulid() for _ in range(5_000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_timestamp_round_trip() -> None:
    assert timestamp_of(ulid_at(1_700_000_000_000)) == 1_700_000_000_000


def test_explicit_ids_sort_by_time() -> None:
    early = ulid_at(1_000, randomness=999)
    late = ulid_at(2_000, randomness=0)
    assert early < late


def test_malformed_ulid_is_rejected() -> None:
    with pytest.raises(ValueError, match="malformed ULID"):
        timestamp_of("too-short")
