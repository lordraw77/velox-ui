"""Encryption of credentials at rest (ADR-0013).

Provider API keys live in the same SQLite file as the conversations, which users copy
around and back up. They are encrypted with AES-256-GCM under a key derived from
``VELOX_SECRET_KEY``; the derivation is domain-separated from the JWT signing key so
the two cannot be substituted for one another.

This protects a database file at rest. It is not a defence against an attacker who
already controls the host, and the documentation says so rather than implying more.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from velox_ui.errors import ErrorCode, VeloxError

__all__ = ["SecretBox", "SecretDecryptionError", "mask_secret"]

_NONCE_BYTES: Final = 12
_MASK_TAIL: Final = 4


class SecretDecryptionError(VeloxError):
    """Raised when a stored secret cannot be decrypted with the current key."""

    code = ErrorCode.INTERNAL
    status_code = 500


class SecretBox:
    """Encrypts and decrypts stored credentials.

    Args:
        secret_key: The master secret from configuration.
    """

    __slots__ = ("_aead",)

    def __init__(self, secret_key: str) -> None:
        """Derive the data key and prepare the AEAD."""
        key = hmac.new(
            secret_key.encode("utf-8"), b"velox:secretbox:v1", hashlib.sha256
        ).digest()
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, *, ref: str) -> tuple[bytes, bytes]:
        """Encrypt a credential.

        Args:
            plaintext: The credential.
            ref: The row key this secret is stored under. It is authenticated as
                associated data, so a ciphertext cannot be moved from one provider's
                row to another's.

        Returns:
            A ``(nonce, ciphertext)`` pair.
        """
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext.encode("utf-8"), ref.encode("utf-8"))
        return nonce, ciphertext

    def decrypt(self, nonce: bytes, ciphertext: bytes, *, ref: str) -> str:
        """Decrypt a credential.

        Args:
            nonce: The nonce stored alongside the ciphertext.
            ciphertext: The stored ciphertext.
            ref: The row key, authenticated as associated data.

        Returns:
            The plaintext credential.

        Raises:
            SecretDecryptionError: If the key is wrong or the record was tampered with.
                The most common real cause is a lost or rotated ``VELOX_SECRET_KEY``,
                so the message says that instead of "invalid tag".
        """
        try:
            return self._aead.decrypt(nonce, ciphertext, ref.encode("utf-8")).decode("utf-8")
        except InvalidTag as exc:
            raise SecretDecryptionError(
                "A stored credential could not be decrypted. This usually means "
                "VELOX_SECRET_KEY changed since it was saved; re-enter the credential.",
                ref=ref,
            ) from exc


def mask_secret(plaintext: str) -> str:
    """Render a credential for display.

    Keeps any recognisable vendor prefix and the last few characters, which is enough
    for a human to tell two keys apart and not enough to use either.

    Args:
        plaintext: The credential.

    Returns:
        A masked string such as ``sk-...4f2a``.
    """
    if len(plaintext) <= _MASK_TAIL * 2:
        return "…"
    head = plaintext[:3] if "-" in plaintext[:4] or "_" in plaintext[:4] else plaintext[:2]
    return f"{head}…{plaintext[-_MASK_TAIL:]}"
