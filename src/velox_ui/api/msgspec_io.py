"""msgspec request and response helpers for the hot path.

FastAPI was chosen for its ecosystem on the condition that Pydantic stays off the
latency-critical path (ADR-0001). These helpers are the other half of that bargain:
routes that run during a completion turn decode their request body and encode their
response through msgspec directly, declaring no ``response_model`` at all.

The decode helper also turns a malformed body into the project's own
:class:`~velox_ui.errors.ValidationError`, so a hand-written route produces the same
error envelope as a Pydantic-validated one.
"""

from __future__ import annotations

from typing import Any

import msgspec
from fastapi import Request, Response

from velox_ui.errors import ValidationError

__all__ = ["json_response", "read_struct"]


_encoder = msgspec.json.Encoder()


async def read_struct[T](request: Request, struct: type[T]) -> T:
    """Decode a request body into a msgspec struct.

    Args:
        request: The incoming request.
        struct: The target struct type.

    Returns:
        The decoded value.

    Raises:
        ValidationError: If the body is not valid JSON or does not match the struct.
    """
    raw = await request.body()
    try:
        return msgspec.json.decode(raw, type=struct)
    except msgspec.ValidationError as exc:
        raise ValidationError(f"The request is not valid: {exc}") from exc
    except msgspec.DecodeError as exc:
        raise ValidationError("The request body is not valid JSON.") from exc


def json_response(payload: Any, *, status_code: int = 200) -> Response:
    """Encode a response body with msgspec, bypassing FastAPI's serialisation."""
    return Response(
        content=_encoder.encode(payload),
        media_type="application/json",
        status_code=status_code,
    )
