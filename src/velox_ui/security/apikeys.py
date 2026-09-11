"""Programmatic API keys.

A key is shown to the user exactly once, at creation, and stored only as a hash. The
printable form is ``velox_sk_<random>``: the prefix makes a leaked key recognisable in
a log or a repository scan, and the first characters are kept in clear as a ``prefix``
column so the UI can show which key is which without being able to reconstruct it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

__all__ = ["KEY_PREFIX", "GeneratedKey", "generate_api_key", "hash_api_key", "mask_key"]

KEY_PREFIX: Final = "velox_sk_"
_ENTROPY_BYTES: Final = 32
_VISIBLE_PREFIX_LENGTH: Final = 8


class GeneratedKey:
    """A freshly generated API key.

    Attributes:
        plaintext: The full key. Returned to the caller once and never stored.
        key_hash: SHA-256 of the plaintext, stored in the database.
        prefix: Leading characters kept in clear for display.
    """

    __slots__ = ("key_hash", "plaintext", "prefix")

    def __init__(self, plaintext: str, key_hash: str, prefix: str) -> None:
        """Store the three forms of the key."""
        self.plaintext = plaintext
        self.key_hash = key_hash
        self.prefix = prefix


def generate_api_key() -> GeneratedKey:
    """Generate a new API key and its stored representation."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(_ENTROPY_BYTES)
    body = plaintext[len(KEY_PREFIX) :]
    return GeneratedKey(
        plaintext=plaintext,
        key_hash=hash_api_key(plaintext),
        prefix=body[:_VISIBLE_PREFIX_LENGTH],
    )


def hash_api_key(plaintext: str) -> str:
    """Hash an API key for storage and lookup.

    SHA-256 rather than a password KDF: the key is high-entropy random data, and every
    authenticated request would otherwise pay the KDF cost.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def looks_like_api_key(candidate: str) -> bool:
    """Whether a bearer credential is an API key rather than a JWT."""
    return candidate.startswith(KEY_PREFIX)


def keys_equal(left: str, right: str) -> bool:
    """Compare two key hashes without leaking their contents through timing."""
    return hmac.compare_digest(left, right)


def mask_key(prefix: str) -> str:
    """Render a key for display, given its stored prefix."""
    return f"{KEY_PREFIX}{prefix}…"
