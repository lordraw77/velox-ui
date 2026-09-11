"""Refresh-token and API-key repository.

Refresh tokens rotate on every use. The rotation is what makes theft detectable: a
used token is marked revoked, and if it is ever presented again the entire family is
revoked, which logs out the attacker and the legitimate user together. That is the
correct outcome — one of them is an intruder and we cannot tell which.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import ApiKey, RefreshToken
from velox_ui.ids import new_ulid
from velox_ui.security.apikeys import GeneratedKey
from velox_ui.security.tokens import hash_refresh_token, new_refresh_token

__all__ = ["CredentialRepository", "RefreshOutcome"]


class RefreshOutcome:
    """Result of presenting a refresh token.

    Attributes:
        user_id: Owner of the token, when it was valid.
        family_id: The rotation family.
        token: The replacement token, when rotation succeeded.
        reused: Whether an already-rotated token was presented, meaning the family was
            revoked as a precaution.
    """

    __slots__ = ("family_id", "reused", "token", "user_id")

    def __init__(
        self,
        *,
        user_id: str | None = None,
        family_id: str | None = None,
        token: str | None = None,
        reused: bool = False,
    ) -> None:
        """Store the outcome fields."""
        self.user_id = user_id
        self.family_id = family_id
        self.token = token
        self.reused = reused

    @property
    def ok(self) -> bool:
        """Whether a new token was issued."""
        return self.token is not None


class CredentialRepository:
    """Reads and writes refresh tokens and API keys.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def issue_refresh_token(
        self,
        *,
        user_id: str,
        ttl_s: int,
        family_id: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, str]:
        """Create a refresh token.

        Args:
            user_id: Owner of the token.
            ttl_s: Lifetime in seconds.
            family_id: Existing family to continue, or ``None`` to start one. A new
                family is a new login; continuing one is a rotation.
            user_agent: Client user agent, for the session list in the UI.

        Returns:
            A tuple of the plaintext token and its family id.
        """
        family = family_id or new_ulid()
        token = new_refresh_token()
        self._session.add(
            RefreshToken(
                id=new_ulid(),
                user_id=user_id,
                token_hash=hash_refresh_token(token),
                family_id=family,
                expires_at=now_ms() + ttl_s * 1_000,
                revoked_at=None,
                user_agent=user_agent[:512] if user_agent else None,
                created_at=now_ms(),
            )
        )
        return token, family

    async def rotate_refresh_token(
        self, token: str, *, ttl_s: int, user_agent: str | None = None
    ) -> RefreshOutcome:
        """Validate a refresh token and replace it.

        Args:
            token: The plaintext token presented by the client.
            ttl_s: Lifetime of the replacement.
            user_agent: Client user agent.

        Returns:
            A :class:`RefreshOutcome`. An unknown or expired token yields an outcome
            with ``ok`` false; a reused one additionally sets ``reused`` after
            revoking the family.
        """
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(token))
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return RefreshOutcome()

        if row.revoked_at is not None:
            await self.revoke_family(row.family_id)
            return RefreshOutcome(user_id=row.user_id, family_id=row.family_id, reused=True)

        if row.expires_at <= now_ms():
            return RefreshOutcome(user_id=row.user_id, family_id=row.family_id)

        row.revoked_at = now_ms()
        replacement, family = await self.issue_refresh_token(
            user_id=row.user_id, ttl_s=ttl_s, family_id=row.family_id, user_agent=user_agent
        )
        return RefreshOutcome(user_id=row.user_id, family_id=family, token=replacement)

    async def revoke_family(self, family_id: str) -> None:
        """Revoke every live token in a rotation family."""
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now_ms())
        )

    async def revoke_token(self, token: str) -> None:
        """Revoke a single token, used by logout."""
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_refresh_token(token),
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now_ms())
        )

    async def purge_expired(self) -> int:
        """Delete tokens that expired more than a day ago.

        Returns:
            The number of rows removed.
        """
        cutoff = now_ms() - 86_400_000
        result = await self._session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def create_api_key(
        self, *, user_id: str, name: str, generated: GeneratedKey, expires_at: int | None = None
    ) -> ApiKey:
        """Store a generated API key.

        Args:
            user_id: Owner.
            name: Label shown in the UI.
            generated: The generated key; only its hash and prefix are persisted.
            expires_at: Optional expiry in epoch milliseconds.

        Returns:
            The pending row.
        """
        record = ApiKey(
            id=new_ulid(),
            user_id=user_id,
            name=name,
            prefix=generated.prefix,
            key_hash=generated.key_hash,
            scopes=None,
            last_used_at=None,
            expires_at=expires_at,
            created_at=now_ms(),
        )
        self._session.add(record)
        return record

    async def api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Look an API key up by hash, ignoring expired ones."""
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash).limit(1)
        record = (await self._session.execute(stmt)).scalar_one_or_none()
        if record is None or (record.expires_at is not None and record.expires_at <= now_ms()):
            return None
        return record

    async def list_api_keys(self, user_id: str) -> Sequence[ApiKey]:
        """List a user's API keys, newest first."""
        stmt = select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.id.desc())
        return (await self._session.execute(stmt)).scalars().all()

    async def delete_api_key(self, user_id: str, key_id: str) -> bool:
        """Delete one of a user's API keys.

        Returns:
            ``True`` if a row was removed.
        """
        result = await self._session.execute(
            delete(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
        )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def touch_api_key(self, key_id: str) -> None:
        """Record that an API key was just used."""
        await self._session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=now_ms())
        )
