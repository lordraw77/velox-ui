"""ULID generation.

Identifiers are 26-character Crockford base32 ULIDs: a 48-bit millisecond timestamp
followed by 80 bits of randomness. Three properties matter here:

* They sort by creation time, so ``ORDER BY id`` is a usable tiebreaker in the keyset
  pagination indexes (ADR-0007) and inserts stay at the right edge of the B-tree.
* They are generated in-process, so a message id exists before any database round
  trip. That is what lets the streaming path start writing to the client while the
  row is still being inserted (ADR-0005).
* They are monotonic within a millisecond, so two messages created in the same
  millisecond still order deterministically.
"""

from __future__ import annotations

import os
import threading

from velox_ui.clock import now_ms

__all__ = ["ULID_LENGTH", "new_ulid", "timestamp_of", "ulid_at"]

ULID_LENGTH = 26

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {char: index for index, char in enumerate(_ALPHABET)}
_RANDOM_BITS = 80
_RANDOM_MASK = (1 << _RANDOM_BITS) - 1

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int) -> str:
    """Encode a 128-bit integer as 26 Crockford base32 characters."""
    out = ["0"] * ULID_LENGTH
    for index in range(ULID_LENGTH - 1, -1, -1):
        out[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def new_ulid() -> str:
    """Return a fresh, monotonically increasing ULID.

    Returns:
        A 26-character uppercase ULID string.
    """
    global _last_ms, _last_random
    moment = now_ms()
    with _lock:
        if moment == _last_ms:
            # Same millisecond: increment instead of drawing a new random suffix, so
            # ordering stays strict. Overflow rolls into the next millisecond.
            _last_random = (_last_random + 1) & _RANDOM_MASK
            if _last_random == 0:
                _last_ms += 1
                moment = _last_ms
        else:
            if moment < _last_ms:
                # The wall clock went backwards (NTP step). Never emit a smaller id.
                moment = _last_ms
            _last_ms = moment
            _last_random = int.from_bytes(os.urandom(10), "big")
        randomness = _last_random
    return _encode((moment << _RANDOM_BITS) | randomness)


def ulid_at(milliseconds: int, *, randomness: int = 0) -> str:
    """Build a ULID with an explicit timestamp.

    Only for tests and benchmark fixtures that need reproducible, time-ordered ids.

    Args:
        milliseconds: Epoch milliseconds to embed in the identifier.
        randomness: The 80-bit suffix. Callers generating many ids should pass an
            incrementing counter to keep them ordered.

    Returns:
        A 26-character ULID string.
    """
    return _encode(
        ((milliseconds & ((1 << 48) - 1)) << _RANDOM_BITS) | (randomness & _RANDOM_MASK)
    )


def timestamp_of(ulid: str) -> int:
    """Extract the embedded epoch-millisecond timestamp from a ULID.

    Args:
        ulid: A 26-character ULID string.

    Returns:
        Milliseconds since the Unix epoch.

    Raises:
        ValueError: If the string is not a well-formed ULID.
    """
    if len(ulid) != ULID_LENGTH:
        raise ValueError(f"malformed ULID: expected {ULID_LENGTH} characters")
    value = 0
    try:
        for char in ulid[:10]:
            value = (value << 5) | _DECODE[char]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"malformed ULID: invalid character {exc.args[0]!r}") from exc
    return value
