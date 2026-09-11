"""Migration control.

Alembic costs roughly a tenth of the cold-start budget just to import, and on the
overwhelmingly common startup — an installation whose schema is already current — it
has nothing to do. So this module answers "is the schema current?" without importing
Alembic at all: it reads the stamped revision with one small query and works out the
head by scanning the migration scripts' headers. Alembic is imported only when there is
actually a migration to apply, or when the script graph is not a simple line and the
cheap answer cannot be trusted.

The scan is deliberately conservative. Anything it cannot resolve unambiguously falls
through to Alembic, so the fast path can only ever skip work that was genuinely
unnecessary — never apply the wrong thing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from alembic.config import Config

__all__ = [
    "current_revision",
    "ensure_schema",
    "head_revision",
    "is_up_to_date",
    "upgrade_to_head",
]

_log = logging.getLogger("velox.db.migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_VERSIONS_DIR = MIGRATIONS_DIR / "versions"

_REVISION_RE = re.compile(r"^revision:\s*str\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_DOWN_RE = re.compile(r"^down_revision:[^=]*=\s*(?:['\"]([^'\"]+)['\"]|None)", re.MULTILINE)


def _scan_script_graph() -> tuple[set[str], set[str]] | None:
    """Read revision identifiers out of the migration scripts.

    Returns:
        A pair of (all revisions, revisions that some script builds on), or ``None``
        if any script could not be parsed — in which case the caller must fall back to
        Alembic rather than guess.
    """
    revisions: set[str] = set()
    parents: set[str] = set()
    try:
        scripts = sorted(_VERSIONS_DIR.glob("*.py"))
    except OSError:
        return None
    if not scripts:
        return None

    for script in scripts:
        try:
            source = script.read_text(encoding="utf-8")
        except OSError:
            return None
        revision = _REVISION_RE.search(source)
        if revision is None:
            return None
        revisions.add(revision.group(1))
        down = _DOWN_RE.search(source)
        if down is None:
            return None
        if down.group(1) is not None:
            parents.add(down.group(1))
    return revisions, parents


def head_revision() -> str | None:
    """Return the single head revision of the migration scripts.

    Returns:
        The head, or ``None`` when the history branches or cannot be parsed. Callers
        treat ``None`` as "ask Alembic".
    """
    scanned = _scan_script_graph()
    if scanned is None:
        return None
    revisions, parents = scanned
    leaves = revisions - parents
    return next(iter(leaves)) if len(leaves) == 1 else None


async def current_revision(engine: AsyncEngine) -> str | None:
    """Return the revision the database is stamped with.

    Reads ``alembic_version`` directly. A missing table means the database has never
    been migrated, which is reported as ``None`` rather than an error.
    """
    async with engine.connect() as connection:
        try:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        except Exception:
            return None
        row = result.first()
        return str(row[0]) if row else None


async def is_up_to_date(engine: AsyncEngine) -> bool:
    """Whether the schema matches the newest migration.

    Used by the readiness probe, so a half-migrated instance never takes traffic.
    """
    head = head_revision()
    if head is None:
        return await _alembic_is_up_to_date(engine)
    return await current_revision(engine) == head


async def ensure_schema(engine: AsyncEngine, url: str) -> bool:
    """Bring the schema up to date, doing nothing when it already is.

    Args:
        engine: The application's engine, reused so no second connection pool is built.
        url: The database URL, needed by Alembic when a migration does run.

    Returns:
        ``True`` when migrations were applied.
    """
    head = head_revision()
    if head is not None and await current_revision(engine) == head:
        return False
    await upgrade_to_head(url)
    return True


async def upgrade_to_head(url: str) -> None:
    """Apply every pending migration.

    Alembic's API is synchronous and drives its own event loop through ``env.py``, so
    it runs in a worker thread.

    Args:
        url: The database URL to migrate.
    """
    import asyncio

    from alembic import command

    _log.info("applying database migrations")
    await asyncio.to_thread(command.upgrade, _alembic_config(url), "head")


def _alembic_config(url: str) -> Config:
    """Build an Alembic configuration bound to one database URL."""
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic_is_up_to_date(engine: AsyncEngine) -> bool:
    """Fall back to Alembic to resolve the head of a branched history."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(MIGRATIONS_DIR))
    return await current_revision(engine) == script.get_current_head()
