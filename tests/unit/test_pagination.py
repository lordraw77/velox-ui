"""Keyset cursor encoding."""

from __future__ import annotations

import pytest

from velox_ui.api.pagination import MAX_PAGE_SIZE, clamp_limit, decode_cursor, encode_cursor
from velox_ui.errors import ValidationError


def test_round_trip() -> None:
    original = (True, 1_700_000_000_000, "01ABCDEF")
    assert decode_cursor(encode_cursor(original), arity=3) == original


def test_cursor_is_url_safe() -> None:
    cursor = encode_cursor((False, 1, "x" * 26))
    assert "=" not in cursor and "+" not in cursor and "/" not in cursor


def test_wrong_arity_is_rejected() -> None:
    # A cursor minted for another list must fail here, not produce a confusing SQL error.
    cursor = encode_cursor((1, 2))
    with pytest.raises(ValidationError):
        decode_cursor(cursor, arity=3)


def test_garbage_is_rejected() -> None:
    with pytest.raises(ValidationError):
        decode_cursor("not-a-cursor!!", arity=2)


def test_limit_is_clamped() -> None:
    assert clamp_limit(None) > 0
    assert clamp_limit(0) == 1
    assert clamp_limit(10_000) == MAX_PAGE_SIZE
