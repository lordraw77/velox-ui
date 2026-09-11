"""Secret redaction in logs."""

from __future__ import annotations

import logging

import pytest

from velox_ui.logging import REDACTED, RedactionFilter, forget_secret, register_secret

SECRET_VALUE = "sk-live-supersecret-value"


@pytest.fixture(autouse=True)
def _forget() -> None:
    yield
    forget_secret(SECRET_VALUE)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("velox.test", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_secret_is_removed_from_the_message() -> None:
    register_secret(SECRET_VALUE)
    record = _record(f"calling with {SECRET_VALUE}")
    RedactionFilter().filter(record)
    assert SECRET_VALUE not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_secret_is_removed_from_structured_fields() -> None:
    register_secret(SECRET_VALUE)
    record = _record("calling", api_key=SECRET_VALUE)
    RedactionFilter().filter(record)
    assert record.__dict__["api_key"] == REDACTED


def test_short_values_are_not_registered() -> None:
    # Redacting a short string would corrupt unrelated output.
    register_secret("abc")
    record = _record("abc is a common substring")
    RedactionFilter().filter(record)
    assert "abc" in record.getMessage()
