"""Plugin error taxonomy.

A disabled or unconfigured plugin and a backend that failed to answer are different
situations for a client to react to (one is "turn it on in settings", the other is
"try again later"), so they get different codes rather than a shared bare exception.
"""

from __future__ import annotations

from velox_ui.errors import ErrorCode, VeloxError

__all__ = ["PluginDisabledError", "PluginUpstreamError"]


class PluginDisabledError(VeloxError):
    """The requested plugin is not enabled or not configured."""

    code = ErrorCode.UNSUPPORTED_CAPABILITY
    status_code = 501


class PluginUpstreamError(VeloxError):
    """The plugin's configured backend failed to answer."""

    code = ErrorCode.UPSTREAM_ERROR
    status_code = 502
    retryable = True
