"""User repository."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import AppUser
from velox_ui.ids import new_ulid

__all__ = ["UserRepository", "normalize_email"]


def normalize_email(email: str) -> str:
    """Normalize an address for lookup and uniqueness.

    Only case and surrounding whitespace are normalized. Stripping dots or ``+tag``
    suffixes would be wrong: whether those are significant is the mail provider's
    decision, not ours.

    Args:
        email: The address as typed.

    Returns:
        The normalized form stored in ``app_user.email_norm``.
    """
    return email.strip().lower()


class UserRepository:
    """Reads and writes :class:`~velox_ui.db.models.AppUser` rows.

    Args:
        session: The session to operate in. The caller decides whether it is a read
            session or a write transaction.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def by_id(self, user_id: str) -> AppUser | None:
        """Return a user by primary key, or ``None``."""
        return await self._session.get(AppUser, user_id)

    async def by_email(self, email: str) -> AppUser | None:
        """Return a user by address, or ``None``."""
        stmt = select(AppUser).where(AppUser.email_norm == normalize_email(email)).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count(self) -> int:
        """Return the number of accounts. Used to decide first-run bootstrap."""
        return int((await self._session.execute(select(func.count(AppUser.id)))).scalar_one())

    async def create(
        self,
        *,
        email: str,
        name: str,
        password_hash: str | None,
        role: str = "user",
        status: str = "active",
    ) -> AppUser:
        """Insert a new account.

        Args:
            email: Address as typed by the user; stored verbatim and normalized.
            name: Display name.
            password_hash: Argon2id hash, or ``None`` for an OIDC-only account.
            role: ``admin`` or ``user``.
            status: ``active``, ``pending`` or ``disabled``.

        Returns:
            The pending :class:`AppUser`. The caller's transaction commits it.
        """
        user = AppUser(
            id=new_ulid(),
            email=email.strip(),
            email_norm=normalize_email(email),
            name=name.strip(),
            password_hash=password_hash,
            role=role,
            status=status,
            avatar_url=None,
            settings=None,
            created_at=now_ms(),
            last_seen_at=None,
        )
        self._session.add(user)
        return user

    async def set_password(self, user_id: str, password_hash: str) -> None:
        """Replace an account's password hash."""
        await self._session.execute(
            update(AppUser).where(AppUser.id == user_id).values(password_hash=password_hash)
        )

    async def touch_last_seen(self, user_id: str) -> None:
        """Record that the account was just active.

        Deliberately a bare ``UPDATE`` with no read: it runs on authenticated requests
        and must not pull the row into the identity map.
        """
        await self._session.execute(
            update(AppUser).where(AppUser.id == user_id).values(last_seen_at=now_ms())
        )
