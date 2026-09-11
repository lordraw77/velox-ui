"""Application state.

One object holds everything created at startup and shared by every request: the
settings, the database, the secret box, and the set of background tasks. It is attached
to ``app.state`` so nothing has to reach for a module-level global, which is what makes
it possible to run several independent instances inside one test process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from velox_ui.db.engine import Database
from velox_ui.security.crypto import SecretBox
from velox_ui.settings import Settings

__all__ = ["AppState"]

_log = logging.getLogger("velox.state")


class AppState:
    """Long-lived objects shared across requests.

    Attributes:
        settings: The validated configuration.
        db: The database.
        secrets: The credential encryption box.
        local_user_id: The built-in account used when authentication is disabled.
        started_at_ms: When the process finished starting, for uptime reporting.
    """

    __slots__ = ("_tasks", "db", "local_user_id", "secrets", "settings", "started_at_ms")

    def __init__(self, settings: Settings, db: Database) -> None:
        """Build the state around an already-created database."""
        self.settings = settings
        self.db = db
        self.secrets = SecretBox(settings.secret_key)
        self.local_user_id: str | None = None
        self.started_at_ms = 0
        self._tasks: set[asyncio.Task[Any]] = set()

    def schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine in the background without awaiting it.

        A strong reference is kept until completion, because the event loop only holds
        a weak one and an unreferenced task can be garbage collected mid-flight.
        Failures are logged rather than swallowed: a background write that silently
        disappears is the kind of bug that surfaces days later as missing data.
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[Any]) -> None:
        """Drop the reference and report an unexpected failure."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _log.error("background task failed", exc_info=error)

    async def drain(self, grace_s: float = 5.0) -> None:
        """Wait for outstanding background tasks during shutdown."""
        if not self._tasks:
            return
        pending = list(self._tasks)
        done, still_running = await asyncio.wait(pending, timeout=grace_s)
        del done
        for task in still_running:
            task.cancel()
