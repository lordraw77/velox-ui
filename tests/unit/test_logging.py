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


def test_no_log_call_uses_a_reserved_record_attribute() -> None:
    """Guard against a bug this project already shipped once.

    ``logger.warning(..., extra={"message": x})`` does not log oddly — Python's
    logging module raises ``KeyError`` because ``message`` is a ``LogRecord``
    attribute. In an exception handler that turns a typed, actionable error into an
    opaque 500. The names are not guessable by reading the logging docs casually, so
    this scans for them instead of trusting review.
    """
    import ast
    import pathlib

    reserved = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    offenders: list[str] = []
    for path in pathlib.Path("src/velox_ui").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and key.value in reserved:
                        offenders.append(f"{path}:{node.lineno} extra={{'{key.value}': ...}}")

    assert not offenders, (
        "these log calls pass a reserved LogRecord attribute in `extra`, which raises "
        f"KeyError at runtime: {offenders}"
    )
