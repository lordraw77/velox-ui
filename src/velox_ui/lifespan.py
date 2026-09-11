"""Startup and shutdown.

Startup does exactly four things and nothing optional: open the database, bring the
schema up to date, make sure an account exists to sign in with, and record the start
time. Everything heavier — provider clients, background workers, RAG — is created by
the phase that introduces it, and only when it is enabled, because every import on this
path is spent against the one-second cold-start budget.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from velox_ui.clock import now_ms
from velox_ui.db.engine import Database
from velox_ui.db.migrate import ensure_schema
from velox_ui.db.repositories.users import UserRepository
from velox_ui.security.password import hash_password
from velox_ui.settings import Settings
from velox_ui.state import AppState

__all__ = ["build_state", "lifespan_for"]

_log = logging.getLogger("velox.lifespan")

LOCAL_USER_EMAIL = "local@velox.local"


async def build_state(settings: Settings) -> AppState:
    """Create the application state and prepare the database.

    Args:
        settings: Validated configuration.

    Returns:
        A ready :class:`~velox_ui.state.AppState`.
    """
    database = Database(
        settings.database_url, echo=settings.db.echo, pool_size=settings.db.pool_size
    )
    if settings.db.auto_migrate:
        # Cheap when there is nothing to do, which is every startup after the first:
        # Alembic is not even imported in that case (see velox_ui.db.migrate).
        await ensure_schema(database.engine, settings.database_url)

    state = AppState(settings, database)
    await _bootstrap_accounts(state)
    state.started_at_ms = now_ms()
    return state


async def _bootstrap_accounts(state: AppState) -> None:
    """Ensure the instance has an account that can sign in.

    Three cases, and no fourth:

    * **Authentication disabled.** A single built-in local account owns everything, so
      the data model stays identical between the single-user and multi-user cases
      instead of growing a nullable owner column.
    * **An administrator password is configured.** The account is created from
      configuration, which is what an unattended deployment needs.
    * **Neither.** Nothing is created. The instance reports ``setup_required`` and the
      first person to register becomes the administrator.

    The third case is why no password is ever generated and written to the log: a
    credential in a log file outlives the convenience it bought, and the first-run
    claim flow gives the same result without one.
    """
    settings = state.settings
    async with state.db.write() as session:
        users = UserRepository(session)

        if not settings.auth.enabled:
            local = await users.by_email(LOCAL_USER_EMAIL)
            if local is None:
                local = await users.create(
                    email=LOCAL_USER_EMAIL,
                    name="Local user",
                    password_hash=None,
                    role="admin",
                )
                await session.flush()
                _log.info("created the built-in local account (authentication is disabled)")
            state.local_user_id = local.id
            return

        if await users.count() > 0 or not settings.auth.admin_password:
            return

        await users.create(
            email=settings.auth.admin_email,
            name="Administrator",
            password_hash=hash_password(settings.auth.admin_password),
            role="admin",
        )
        _log.info("created the configured administrator account %r", settings.auth.admin_email)


def lifespan_for(settings: Settings) -> object:
    """Build the ASGI lifespan handler for an application.

    Args:
        settings: Validated configuration.

    Returns:
        An async context manager suitable for FastAPI's ``lifespan`` argument.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = await build_state(settings)
        app.state.velox = state
        _log.info(
            "velox-ui ready",
            extra={
                "port": settings.port,
                "database": "sqlite" if settings.is_sqlite else "postgresql",
            },
        )
        try:
            yield
        finally:
            await state.drain()
            await state.db.dispose()
            _log.info("velox-ui stopped")

    return lifespan
