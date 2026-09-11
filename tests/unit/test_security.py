"""Password hashing, tokens, API keys and credential encryption."""

from __future__ import annotations

import time

import pytest

from velox_ui.errors import AuthenticationError
from velox_ui.security.apikeys import (
    generate_api_key,
    hash_api_key,
    looks_like_api_key,
    mask_key,
)
from velox_ui.security.crypto import SecretBox, SecretDecryptionError, mask_secret
from velox_ui.security.password import hash_password, verify_password
from velox_ui.security.tokens import (
    decode_access_token,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
)

SECRET = "master-secret-for-tests"


def test_password_round_trip() -> None:
    stored = hash_password("correct-horse-battery")
    assert verify_password(stored, "correct-horse-battery")
    assert not verify_password(stored, "wrong")


def test_missing_hash_still_does_the_work() -> None:
    # An unknown account must not answer faster than a known one, or login latency
    # becomes a user-enumeration oracle.
    start = time.perf_counter()
    assert verify_password(None, "anything") is False
    elapsed = time.perf_counter() - start
    assert elapsed > 0.001, "verification against a missing hash must not short-circuit"


def test_access_token_round_trip() -> None:
    token, expires_at = issue_access_token(
        SECRET, user_id="01USER", role="admin", session_id="01SESSION", ttl_s=60
    )
    claims = decode_access_token(SECRET, token)
    assert claims.user_id == "01USER"
    assert claims.is_admin
    assert claims.session_id == "01SESSION"
    assert expires_at > 0


def test_token_signed_with_another_key_is_rejected() -> None:
    token, _ = issue_access_token(SECRET, user_id="u", role="user", session_id="s", ttl_s=60)
    with pytest.raises(AuthenticationError):
        decode_access_token("a-different-master-secret", token)


def test_expired_token_is_reported_as_expired() -> None:
    token, _ = issue_access_token(SECRET, user_id="u", role="user", session_id="s", ttl_s=-10)
    with pytest.raises(AuthenticationError) as caught:
        decode_access_token(SECRET, token)
    assert caught.value.detail.get("expired") is True, (
        "the client refreshes instead of logging out"
    )


def test_refresh_tokens_are_opaque_and_hashed() -> None:
    token = new_refresh_token()
    assert "." not in token, "a refresh token must not be a JWT"
    assert hash_refresh_token(token) != token
    assert hash_refresh_token(token) == hash_refresh_token(token)


def test_api_key_shape() -> None:
    generated = generate_api_key()
    assert looks_like_api_key(generated.plaintext)
    assert generated.key_hash == hash_api_key(generated.plaintext)
    assert generated.plaintext not in mask_key(generated.prefix)


def test_secret_box_round_trip() -> None:
    box = SecretBox(SECRET)
    nonce, ciphertext = box.encrypt("sk-live-abcdef", ref="provider:01:api_key")
    assert box.decrypt(nonce, ciphertext, ref="provider:01:api_key") == "sk-live-abcdef"


def test_ciphertext_cannot_be_moved_to_another_row() -> None:
    # The row key is authenticated, so a stolen ciphertext pasted into a different
    # provider's row does not decrypt.
    box = SecretBox(SECRET)
    nonce, ciphertext = box.encrypt("sk-live-abcdef", ref="provider:01:api_key")
    with pytest.raises(SecretDecryptionError):
        box.decrypt(nonce, ciphertext, ref="provider:02:api_key")


def test_rotated_master_key_gives_an_actionable_error() -> None:
    nonce, ciphertext = SecretBox(SECRET).encrypt("sk-live-abcdef", ref="r")
    with pytest.raises(SecretDecryptionError, match="VELOX_SECRET_KEY"):
        SecretBox("another-master-secret").decrypt(nonce, ciphertext, ref="r")


def test_masking_hides_the_body() -> None:
    masked = mask_secret("sk-proj-0123456789abcdef")
    assert "0123456789" not in masked
    assert masked.endswith("cdef")
