"""Typed error taxonomy.

Every failure that can reach a client is one of a closed set of codes. Routes never
raise bare ``HTTPException`` with an ad-hoc string: the UI switches on ``code`` to
decide whether to offer a retry, a re-login, a shorter prompt or an offline badge,
and a generic 500 gives it nothing to work with.

The same payload is used for non-streaming responses and for the ``error`` event of
a stream, so the client has one shape to parse (docs/design/03-http-api.md).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

__all__ = [
    "AuthenticationError",
    "ConflictError",
    "ErrorCode",
    "ForbiddenError",
    "NotFoundError",
    "QuotaExceeded",
    "RateLimited",
    "ValidationError",
    "VeloxError",
]


class ErrorCode(StrEnum):
    """Closed set of machine-readable error codes exposed by the API."""

    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_REQUEST = "invalid_request"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_NOT_FOUND = "model_not_found"
    BACKEND_OFFLINE = "backend_offline"
    BACKEND_TIMEOUT = "backend_timeout"
    MODEL_LOADING = "model_loading"
    OUT_OF_MEMORY = "out_of_memory"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    UPSTREAM_ERROR = "upstream_error"
    INTERNAL = "internal"


class VeloxError(Exception):
    """Base class for every error that is safe to show a client.

    Attributes:
        code: The machine-readable code the client switches on.
        message: A human-readable sentence, in English, safe to display verbatim.
        status_code: The HTTP status used when this error terminates a request
            before the response body has started.
        retryable: Whether retrying the identical request could plausibly succeed.
        retry_after_s: Server-advised delay before a retry, when known.
        detail: Extra structured context (provider, model, limits). Must never
            contain secrets; the logging filter redacts known values as a backstop.
    """

    code: ErrorCode = ErrorCode.INTERNAL
    status_code: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        retry_after_s: float | None = None,
        **detail: Any,
    ) -> None:
        """Create an error.

        Args:
            message: Human-readable description, in English.
            code: Overrides the class-level code.
            status_code: Overrides the class-level HTTP status.
            retryable: Overrides the class-level retryability.
            retry_after_s: Server-advised retry delay in seconds.
            **detail: Structured context merged into ``detail``.
        """
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.detail: dict[str, Any] = {
            key: value for key, value in detail.items() if value is not None
        }

    def to_payload(self, *, request_id: str | None = None) -> dict[str, Any]:
        """Render the wire envelope for this error.

        Args:
            request_id: Correlation id echoed back to the client and present in logs.

        Returns:
            A JSON-serializable dict under a single ``error`` key.
        """
        body: dict[str, Any] = {
            "code": str(self.code),
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.retry_after_s is not None:
            body["retry_after_s"] = self.retry_after_s
        if request_id is not None:
            body["request_id"] = request_id
        body.update(self.detail)
        return {"error": body}

    def __repr__(self) -> str:
        """Return a debug representation including the code."""
        return f"{type(self).__name__}(code={self.code!s}, message={self.message!r})"


class AuthenticationError(VeloxError):
    """The caller did not present usable credentials."""

    code = ErrorCode.UNAUTHORIZED
    status_code = 401


class ForbiddenError(VeloxError):
    """The caller is authenticated but not allowed to perform this action."""

    code = ErrorCode.FORBIDDEN
    status_code = 403


class NotFoundError(VeloxError):
    """The addressed resource does not exist, or is not visible to the caller."""

    code = ErrorCode.NOT_FOUND
    status_code = 404


class ConflictError(VeloxError):
    """The request collides with existing state, such as a duplicate unique key."""

    code = ErrorCode.CONFLICT
    status_code = 409


class ValidationError(VeloxError):
    """The request was well-formed HTTP but semantically unusable."""

    code = ErrorCode.INVALID_REQUEST
    status_code = 422


class RateLimited(VeloxError):
    """A rate limit was hit, either ours or an upstream provider's."""

    code = ErrorCode.RATE_LIMITED
    status_code = 429
    retryable = True


class QuotaExceeded(VeloxError):
    """A budget has been exhausted; retrying now will not help."""

    code = ErrorCode.QUOTA_EXCEEDED
    status_code = 429
