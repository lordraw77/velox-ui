"""Plugin configuration repository.

``images`` and ``voice`` (ADR-0014) are process-wide singletons — at most one
configured backend per kind — so their configuration lives as two rows in the
existing ``setting`` table (``key="plugin:images"`` / ``key="plugin:voice"``) rather
than a dedicated table. The credential, if any, is encrypted at rest the same way an
MCP server's or a provider's is: a ``secret`` row referenced by ``auth_ref``, stored
inside the setting's JSON value.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import Secret, Setting

__all__ = ["PluginConfigRepository"]


def _key(kind: str) -> str:
    return f"plugin:{kind}"


class PluginConfigRepository:
    """Reads and writes plugin configuration and its stored credential.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def get(self, kind: str) -> dict[str, Any] | None:
        """Return the stored configuration dict for a plugin kind, or ``None``."""
        row = await self._session.get(Setting, _key(kind))
        return dict(row.value) if row is not None else None

    async def put(self, kind: str, value: dict[str, Any]) -> None:
        """Replace the stored configuration dict for a plugin kind."""
        row = await self._session.get(Setting, _key(kind))
        if row is None:
            self._session.add(Setting(key=_key(kind), value=value, updated_at=now_ms()))
            return
        row.value = value
        row.updated_at = now_ms()

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
