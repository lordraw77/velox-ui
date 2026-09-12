"""Provider error taxonomy.

Every failure a backend can produce becomes one of these before it leaves the provider
layer. The service layer switches on the type, never on a string message, to decide
whether to retry, fail over, or surface a specific UI state — and critically, whether a
retry is even safe: retrying a :class:`ModelLoading` would trigger a second load of the
same weights on a host that may already be struggling (ADR-0008).

These map onto :class:`velox_ui.errors.ErrorCode` at the API boundary; they exist
separately because a provider error additionally carries which provider and model
produced it, which the generic taxonomy has no reason to know about.
"""

from __future__ import annotations

from velox_ui.errors import ErrorCode, VeloxError

__all__ = [
    "AuthError",
    "BackendOffline",
    "BackendTimeout",
    "ContextOverflow",
    "ModelLoading",
    "ModelNotFound",
    "OutOfMemory",
    "ProviderError",
    "QuotaExceeded",
    "RateLimited",
    "UnsupportedCapability",
    "UpstreamError",
]


class ProviderError(VeloxError):
    """Base class for every error originating from a backend call.

    Attributes:
        provider_id: Which configured provider raised this.
        model: Which model was being used, when known.
    """

    code = ErrorCode.UPSTREAM_ERROR
    status_code = 502
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        model: str | None = None,
        **kwargs: object,
    ) -> None:
        """Attach provider and model context to the error."""
        super().__init__(message, provider=provider_id, model=model, **kwargs)  # type: ignore[arg-type]
        self.provider_id = provider_id
        self.model = model


class AuthError(ProviderError):
    """The backend rejected our credentials."""

    code = ErrorCode.INVALID_CREDENTIALS
    status_code = 401


class RateLimited(ProviderError):
    """The backend is rate-limiting us."""

    code = ErrorCode.RATE_LIMITED
    status_code = 429
    retryable = True


class QuotaExceeded(ProviderError):
    """A budget on the backend side is exhausted; retrying will not help."""

    code = ErrorCode.QUOTA_EXCEEDED
    status_code = 429


class ContextOverflow(ProviderError):
    """The request exceeds the model's context window."""

    code = ErrorCode.CONTEXT_OVERFLOW
    status_code = 422


class ModelNotFound(ProviderError):
    """The requested model is not known to this backend."""

    code = ErrorCode.MODEL_NOT_FOUND
    status_code = 404


class BackendOffline(ProviderError):
    """The backend could not be reached at all: refused, DNS failure, host down.

    This is an ordinary, expected state for a local backend (ADR-0008), not a bug.
    """

    code = ErrorCode.BACKEND_OFFLINE
    status_code = 503
    retryable = True


class BackendTimeout(ProviderError):
    """A configured timeout elapsed."""

    code = ErrorCode.BACKEND_TIMEOUT
    status_code = 504
    retryable = True


class ModelLoading(ProviderError):
    """The backend is loading model weights.

    Never retried: retrying would ask the backend to load the same weights a second
    time, which on a slow disk makes the problem worse, not better.
    """

    code = ErrorCode.MODEL_LOADING
    status_code = 503
    retryable = False


class OutOfMemory(ProviderError):
    """The backend ran out of VRAM or RAM serving the request."""

    code = ErrorCode.OUT_OF_MEMORY
    status_code = 503


class UnsupportedCapability(ProviderError):
    """The requested operation is not something this backend/model can do."""

    code = ErrorCode.UNSUPPORTED_CAPABILITY
    status_code = 422


class UpstreamError(ProviderError):
    """Anything else. Carries whatever detail the backend gave us, never a raw body."""

    code = ErrorCode.UPSTREAM_ERROR
    status_code = 502
