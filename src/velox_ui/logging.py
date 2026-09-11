"""Structured logging with secret redaction.

Logs are JSON by default so a deployment can ship them anywhere without a parser,
and human-readable when a terminal is attached.

Every record passes through :class:`RedactionFilter`. Provider API keys are registered
with :func:`register_secret` the moment they are decrypted, and any log line, exception
message or upstream error body that contains one is rewritten before it is emitted.
This is a backstop, not a licence to log credentials: the rule remains that secrets are
never passed to a logger in the first place (ADR-0013).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import msgspec

__all__ = [
    "REDACTED",
    "RedactionFilter",
    "configure_logging",
    "forget_secret",
    "register_secret",
]

REDACTED = "[redacted]"
_MIN_SECRET_LENGTH = 8

_secrets: set[str] = set()

_RESERVED = frozenset(
    {
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
        "module",
        "msecs",
        "message",
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
)


def register_secret(value: str | None) -> None:
    """Register a value that must never appear in a log line.

    Args:
        value: The secret. Values shorter than eight characters are ignored, because
            redacting a short common string would corrupt unrelated log output.
    """
    if value and len(value) >= _MIN_SECRET_LENGTH:
        _secrets.add(value)


def forget_secret(value: str | None) -> None:
    """Stop redacting a value, for example after a provider is deleted."""
    if value:
        _secrets.discard(value)


def _scrub(text: str) -> str:
    """Replace every registered secret occurring in ``text``."""
    for secret in _secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


class RedactionFilter(logging.Filter):
    """Rewrite registered secrets out of the message and structured fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub the record in place and always allow it through."""
        if not _secrets:
            return True
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: _scrub(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    _scrub(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED and isinstance(value, str):
                record.__dict__[key] = _scrub(value)
        return True


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record, including any extra fields the caller attached."""
        payload: dict[str, Any] = {
            "ts": int(record.created * 1_000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = _scrub(self.formatException(record.exc_info))
        return msgspec.json.encode(payload).decode("utf-8")


class ConsoleFormatter(logging.Formatter):
    """Compact single-line format for interactive use."""

    def __init__(self) -> None:
        """Configure the underlying format string."""
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
        )

    def format(self, record: logging.LogRecord) -> str:
        """Append extra fields after the message, space separated."""
        base = super().format(record)
        extras = " ".join(
            f"{key}={value!r}"
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        )
        return f"{base} {extras}" if extras else base


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install the root logging configuration.

    Idempotent: calling it again replaces the previous handler, which keeps tests and
    the reloading development server from stacking duplicate handlers.

    Args:
        level: Root log level name.
        json_output: Emit JSON lines instead of the console format.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # These are chatty at INFO and say nothing we do not already log ourselves.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    # Alembic announces every autogenerate plugin it loads, on every startup.
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
