"""Keyset pagination (ADR-0007).

``OFFSET`` is banned on user-facing lists: its cost grows with the offset, so a user
with ten thousand conversations pays for every page they have already scrolled past. A
keyset cursor instead carries the ordering tuple of the last row returned, and the next
page is an indexed seek.

Cursors are opaque — base64url of a msgpack tuple — for one practical reason: the
ordering of a list is an implementation detail, and clients that parse a cursor turn it
into a compatibility constraint.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

import msgspec

from velox_ui.errors import ValidationError

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def encode_cursor(values: tuple[Any, ...]) -> str:
    """Encode an ordering tuple as an opaque cursor.

    Args:
        values: The ordering key of the last row on the current page.

    Returns:
        A URL-safe, unpadded base64 string.
    """
    packed = msgspec.msgpack.encode(values)
    return base64.urlsafe_b64encode(packed).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str, *, arity: int) -> tuple[Any, ...]:
    """Decode an opaque cursor back into its ordering tuple.

    Args:
        cursor: The cursor supplied by the client.
        arity: How many elements the tuple must contain. A cursor minted for a
            different list, or by an older version, is rejected here rather than
            producing a confusing query error later.

    Returns:
        The decoded ordering tuple.

    Raises:
        ValidationError: If the cursor is malformed or has the wrong shape.
    """
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        values = msgspec.msgpack.decode(raw)
    except (binascii.Error, ValueError, msgspec.DecodeError) as exc:
        raise ValidationError("The pagination cursor is not valid.") from exc
    if not isinstance(values, list) or len(values) != arity:
        raise ValidationError("The pagination cursor does not match this list.")
    return tuple(values)


def clamp_limit(limit: int | None) -> int:
    """Clamp a client-supplied page size into the supported range.

    Args:
        limit: Requested page size, or ``None`` for the default.

    Returns:
        A page size between 1 and :data:`MAX_PAGE_SIZE`.
    """
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))
