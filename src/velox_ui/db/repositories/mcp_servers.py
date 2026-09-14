"""MCP server repository.

An MCP server's connection details split across two places: non-secret shape
(transport, command/args/env or url/headers) lives in ``mcp_server.config``; anything
that authenticates — a bearer token, an API key passed as an env var — goes to the
``secret`` table, encrypted at rest (ADR-0013), and is referenced by ``auth_ref``. The
same split ``ProviderRepository`` uses for provider API keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.clock import now_ms
from velox_ui.db.models import McpServer, Secret
from velox_ui.ids import new_ulid

__all__ = ["McpServerRepository"]


class McpServerRepository:
    """Reads and writes :class:`~velox_ui.db.models.McpServer` rows.

    Args:
        session: The session to operate in.
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def create(
        self,
        *,
        owner_id: str,
        name: str,
        transport: str,
        config: dict[str, Any],
        auth_ref: str | None = None,
        approval: str = "always",
        enabled: bool = True,
    ) -> McpServer:
        """Insert a new server."""
        moment = now_ms()
        server = McpServer(
            id=new_ulid(),
            owner_id=owner_id,
            name=name,
            transport=transport,
            config=config,
            auth_ref=auth_ref,
            enabled=enabled,
            approval=approval,
            tool_cache=None,
            created_at=moment,
        )
        self._session.add(server)
        return server

    async def get(self, server_id: str) -> McpServer | None:
        """One server by id, or ``None``."""
        return await self._session.get(McpServer, server_id)

    async def list_visible(self, *, user_id: str) -> Sequence[McpServer]:
        """A user's own servers, plus ones with no owner (shared instance-wide)."""
        stmt = (
            select(McpServer)
            .where(or_(McpServer.owner_id == user_id, McpServer.owner_id.is_(None)))
            .order_by(McpServer.created_at)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def update(
        self,
        server_id: str,
        *,
        owner_id: str,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
        approval: str | None = None,
    ) -> bool:
        """Update a server owned by ``owner_id``. Returns whether it was found."""
        values: dict[str, object] = {}
        if name is not None:
            values["name"] = name
        if config is not None:
            values["config"] = config
        if enabled is not None:
            values["enabled"] = enabled
        if approval is not None:
            values["approval"] = approval
        if not values:
            return await self.get(server_id) is not None
        result = await self._session.execute(
            update(McpServer)
            .where(McpServer.id == server_id, McpServer.owner_id == owner_id)
            .values(**values)
        )
        return bool(cast(CursorResult[object], result).rowcount)

    async def set_tool_cache(self, server_id: str, tools: list[dict[str, Any]]) -> None:
        """Replace a server's cached tool list, after a successful ``connect``."""
        await self._session.execute(
            update(McpServer).where(McpServer.id == server_id).values(tool_cache=tools)
        )

    async def delete(self, server_id: str, *, owner_id: str) -> bool:
        """Delete a server owned by ``owner_id``, including its credential."""
        server = await self.get(server_id)
        if server is None or server.owner_id != owner_id:
            return False
        if server.auth_ref:
            await self.delete_secret(server.auth_ref)
        await self._session.delete(server)
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
