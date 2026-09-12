"""Security response headers.

The policy is written for what this application actually does, not copied from a
template. Every directive below is either required by something real or deliberately
tight, and the two loosenings are called out rather than buried:

* ``style-src-attr 'unsafe-inline'`` — the virtual list positions its window with an
  inline ``transform``, recomputed as the reader scrolls. A nonce cannot be used for a
  value that changes sixty times a second. This allows inline style *attributes* only,
  not inline ``<style>`` elements or scripts, so it does not open a script path.
* ``img-src`` allows remote HTTPS images, because markdown from a model or a retrieved
  document legitimately contains them. Their URLs are already scheme-checked before
  rendering (``lib/markdown/sanitize.ts``).

Notably absent: ``script-src`` has no ``'unsafe-inline'`` and no ``'unsafe-eval'``. The
build emits module scripts as files, so a successful HTML injection still cannot run
code — which is the second line of defence behind escaping raw HTML before markdown is
parsed at all.

``Strict-Transport-Security`` is set only on HTTPS requests. Sending it over plain HTTP
would be ignored by browsers anyway, and a self-hosted instance on a home network is
very often reached over HTTP; asserting HSTS there risks locking someone out of their
own server.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["CONTENT_SECURITY_POLICY", "SecurityHeadersMiddleware"]

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "media-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    # Nothing here uses a camera, a microphone or geolocation. Speech input arrives
    # with the optional voice plugin, which will have to relax this explicitly.
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"x-frame-options", b"DENY"),
)

_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Attach security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add the headers to the response start message."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        secure = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in _HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                if secure and _HSTS[0] not in existing:
                    headers.append(_HSTS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
