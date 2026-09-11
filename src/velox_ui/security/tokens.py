"""Access tokens and refresh tokens.

Access tokens are short-lived JWTs signed with HS256 from a key derived from
``VELOX_SECRET_KEY``; they carry only what authorization needs, so a request can be
authenticated without touching the database.

Refresh tokens are opaque random strings, never JWTs. They are stored as SHA-256
hashes and rotate on every use: presenting a token that was already rotated is treated
as a leak and revokes the entire token family (see :class:`velox_ui.db.models.RefreshToken`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Final

import jwt

from velox_ui.clock import now_ms
from velox_ui.errors import AuthenticationError, ErrorCode

__all__ = [
    "AccessClaims",
    "decode_access_token",
    "hash_refresh_token",
    "issue_access_token",
    "new_refresh_token",
]

_ALGORITHM: Final = "HS256"
_ISSUER: Final = "velox-ui"
_REFRESH_BYTES: Final = 32


class AccessClaims:
    """Validated claims of an access token.

    Attributes:
        user_id: Subject of the token.
        role: ``admin`` or ``user``.
        session_id: Refresh-token family the access token was minted from, so a
            revoked session can be recognised without a database lookup once
            revocation lists are added.
        expires_at_ms: Expiry, in epoch milliseconds.
    """

    __slots__ = ("expires_at_ms", "role", "session_id", "user_id")

    def __init__(self, user_id: str, role: str, session_id: str, expires_at_ms: int) -> None:
        """Store the validated claims."""
        self.user_id = user_id
        self.role = role
        self.session_id = session_id
        self.expires_at_ms = expires_at_ms

    @property
    def is_admin(self) -> bool:
        """Whether the token grants administrator rights."""
        return self.role == "admin"


def _signing_key(secret_key: str) -> bytes:
    """Derive the JWT signing key from the master secret.

    The master secret also encrypts provider credentials. Deriving a separate key per
    purpose means a signing-key disclosure cannot be replayed against the credential
    store.
    """
    return hmac.new(secret_key.encode("utf-8"), b"velox:jwt:v1", hashlib.sha256).digest()


def issue_access_token(
    secret_key: str,
    *,
    user_id: str,
    role: str,
    session_id: str,
    ttl_s: int,
) -> tuple[str, int]:
    """Mint a signed access token.

    Args:
        secret_key: The master secret.
        user_id: Subject of the token.
        role: ``admin`` or ``user``.
        session_id: Refresh-token family id.
        ttl_s: Lifetime in seconds.

    Returns:
        A tuple of the encoded token and its expiry in epoch milliseconds.
    """
    issued_at = now_ms() // 1_000
    expires_at = issued_at + ttl_s
    payload: dict[str, Any] = {
        "iss": _ISSUER,
        "sub": user_id,
        "role": role,
        "sid": session_id,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(payload, _signing_key(secret_key), algorithm=_ALGORITHM)
    return token, expires_at * 1_000


def decode_access_token(secret_key: str, token: str) -> AccessClaims:
    """Verify and decode an access token.

    Args:
        secret_key: The master secret.
        token: The encoded JWT.

    Returns:
        The validated claims.

    Raises:
        AuthenticationError: If the token is expired, malformed or badly signed. The
            message never distinguishes those cases beyond what the client needs, but
            expiry is reported separately so the UI can refresh instead of logging out.
    """
    try:
        payload = jwt.decode(
            token,
            _signing_key(secret_key),
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError(
            "The access token has expired.", code=ErrorCode.UNAUTHORIZED, expired=True
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("The access token is not valid.") from exc

    return AccessClaims(
        user_id=str(payload["sub"]),
        role=str(payload.get("role", "user")),
        session_id=str(payload.get("sid", "")),
        expires_at_ms=int(payload["exp"]) * 1_000,
    )


def new_refresh_token() -> str:
    """Generate an opaque refresh token."""
    return secrets.token_urlsafe(_REFRESH_BYTES)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage.

    A fast hash is correct here: the token is 256 bits of entropy from a CSPRNG, so
    there is nothing to brute-force and a slow KDF would only add login latency.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
