"""Password hashing.

Argon2id, with the reference parameters from ``argon2-cffi``. Two details matter:

* Verification reports whether the stored hash used weaker parameters than the current
  policy, so passwords are transparently re-hashed on a successful login instead of
  being frozen at whatever cost factor was current when the account was created.
* :func:`verify` performs the same work whether or not the account exists. Skipping the
  hash for an unknown email turns login latency into a user-enumeration oracle.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = ["hash_password", "needs_rehash", "verify_password"]

_hasher = PasswordHasher()

# Hash of an unusable password, used to equalize timing for unknown accounts. It is
# built on first use rather than at import: computing it eagerly means every process
# start pays a full Argon2 hash — deliberately expensive work — before it can serve
# anything, and most startups never see a failed login at all.
_dummy_hash: str | None = None


def _timing_equalizer() -> str:
    """Return the dummy hash, computing it once."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash("velox-ui-timing-equalizer")
    return _dummy_hash


def hash_password(password: str) -> str:
    """Hash a plaintext password.

    Args:
        password: The plaintext password.

    Returns:
        An encoded Argon2id hash, safe to store.
    """
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Check a password against a stored hash in constant-ish time.

    Args:
        stored_hash: The stored Argon2id hash, or ``None`` when the account does not
            exist or has no password (OIDC-only). A dummy verification still runs.
        password: The candidate plaintext.

    Returns:
        ``True`` if the password matches.
    """
    candidate = stored_hash if stored_hash else _timing_equalizer()
    try:
        _hasher.verify(candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return bool(stored_hash)


def needs_rehash(stored_hash: str) -> bool:
    """Whether a stored hash should be upgraded to the current parameters."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
