"""Engine and session management.

SQLite is the zero-config default (ADR-0003). Making it behave well under an async
server takes three things, all of them set up here:

* **WAL mode**, so readers never block on the writer.
* **A pragma set applied to every connection**, since SQLite pragmas are per
  connection, not per database.
* **Serialized writes.** SQLite allows exactly one writer; letting several coroutines
  race for it produces ``database is locked`` under load. A single asyncio lock around
  write transactions turns that contention into an orderly queue, which is cheap here
  because writes are small and already off the streaming path (ADR-0005).

PostgreSQL needs none of this and takes the plain path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

__all__ = ["SQLITE_PRAGMAS", "Database"]

SQLITE_PRAGMAS: dict[str, str | int] = {
    "journal_mode": "WAL",
    # NORMAL is the right trade-off under WAL: a crash can lose the last commits but
    # cannot corrupt the database, and FULL would fsync on every small chat write.
    "synchronous": "NORMAL",
    "busy_timeout": 5000,
    "foreign_keys": "ON",
    "temp_store": "MEMORY",
    "mmap_size": 268_435_456,
    "cache_size": -32_000,
}


class Database:
    """Owns the engine, the session factory and the SQLite write lock.

    Attributes:
        engine: The SQLAlchemy async engine.
        is_sqlite: Whether the engine targets SQLite.
    """

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 5) -> None:
        """Create the engine.

        Args:
            url: SQLAlchemy async URL.
            echo: Log every statement. Development only.
            pool_size: Reader pool size. Ignored for in-memory SQLite, which must keep
                a single shared connection or each session would see its own database.
        """
        self.is_sqlite = url.startswith("sqlite")
        kwargs: dict[str, Any] = {"echo": echo, "future": True}
        if self.is_sqlite:
            if ":memory:" in url or "mode=memory" in url:
                kwargs["poolclass"] = StaticPool
                kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = pool_size
            kwargs["pool_pre_ping"] = True

        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        if self.is_sqlite:
            _install_sqlite_pragmas(self.engine)

        self._sessions = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
        )
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Open a read-only session.

        Yields:
            A session that is rolled back on exit. Use :meth:`write` for anything that
            modifies data, so the SQLite writer stays serialized.
        """
        async with self._sessions() as session:
            try:
                yield session
            finally:
                await session.rollback()

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Open a write session, committing on success and rolling back on failure.

        On SQLite the session is taken under a process-wide lock; on PostgreSQL the
        lock is uncontended and effectively free.

        Yields:
            A session inside a transaction.
        """
        lock = self._write_lock if self.is_sqlite else _NULL_LOCK
        async with lock, self._sessions() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close every pooled connection. Called on shutdown."""
        await self.engine.dispose()


class _NullLock:
    """A lock-shaped object that never blocks, used for non-SQLite engines."""

    async def __aenter__(self) -> None:
        """Enter without acquiring anything."""

    async def __aexit__(self, *_: object) -> None:
        """Exit without releasing anything."""


_NULL_LOCK = _NullLock()


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Apply :data:`SQLITE_PRAGMAS` to every new connection.

    The listener is attached to the synchronous engine underneath the async one,
    because that is where the DBAPI connect event is emitted.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            for name, value in SQLITE_PRAGMAS.items():
                cursor.execute(f"PRAGMA {name}={value}")
        finally:
            cursor.close()
