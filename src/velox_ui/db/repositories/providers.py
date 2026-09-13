"""Interface-configured providers and their encrypted credentials."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import ModelParams, Provider, Secret

__all__ = ["ModelParamsRepository", "ProviderRepository"]


class ProviderRepository:
    """Reads and writes stored providers.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def list(self) -> Sequence[Provider]:
        """Every stored provider, in display order."""
        stmt = select(Provider).order_by(Provider.sort_order, Provider.created_at)
        return (await self._session.execute(stmt)).scalars().all()

    async def get(self, provider_id: str) -> Provider | None:
        """One stored provider, or ``None``."""
        return await self._session.get(Provider, provider_id)

    async def create(
        self,
        *,
        provider_id: str,
        name: str,
        kind: str,
        preset: str | None,
        base_url: str,
        is_local: bool,
        auth_ref: str | None,
    ) -> Provider:
        """Insert a provider."""
        moment = now_ms()
        provider = Provider(
            id=provider_id,
            name=name,
            kind=kind,
            preset=preset,
            base_url=base_url,
            auth_ref=auth_ref,
            extra=None,
            enabled=True,
            is_local=is_local,
            sort_order=0,
            created_at=moment,
            updated_at=moment,
        )
        self._session.add(provider)
        return provider

    async def delete(self, provider_id: str) -> bool:
        """Remove a provider and its credential. Returns whether it existed."""
        provider = await self.get(provider_id)
        if provider is None:
            return False
        if provider.auth_ref:
            await self.delete_secret(provider.auth_ref)
        await self._session.delete(provider)
        return True

    async def get_secret(self, ref: str) -> Secret | None:
        """One encrypted credential, or ``None``."""
        return await self._session.get(Secret, ref)

    async def put_secret(self, ref: str, *, nonce: bytes, ciphertext: bytes, hint: str) -> None:
        """Store or replace an encrypted credential."""
        existing = await self.get_secret(ref)
        if existing is None:
            self._session.add(
                Secret(
                    ref=ref, nonce=nonce, ciphertext=ciphertext, hint=hint, updated_at=now_ms()
                )
            )
            return
        existing.nonce = nonce
        existing.ciphertext = ciphertext
        existing.hint = hint
        existing.updated_at = now_ms()

    async def delete_secret(self, ref: str) -> None:
        """Remove a credential, if present."""
        await self._session.execute(delete(Secret).where(Secret.ref == ref))


class ModelParamsRepository:
    """Reads and writes a user's saved per-model parameters.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def get(self, user_id: str, model_ref: str) -> dict[str, Any] | None:
        """The saved parameters, or ``None`` when there are none."""
        row = await self._session.get(ModelParams, (user_id, model_ref))
        return dict(row.params) if row is not None else None

    async def put(self, user_id: str, model_ref: str, params: dict[str, Any]) -> None:
        """Store or replace the parameters for one model."""
        row = await self._session.get(ModelParams, (user_id, model_ref))
        if row is None:
            self._session.add(
                ModelParams(
                    user_id=user_id, model_ref=model_ref, params=params, updated_at=now_ms()
                )
            )
            return
        row.params = params
        row.updated_at = now_ms()

    async def delete(self, user_id: str, model_ref: str) -> None:
        """Forget the parameters for one model."""
        await self._session.execute(
            delete(ModelParams).where(
                ModelParams.user_id == user_id, ModelParams.model_ref == model_ref
            )
        )
