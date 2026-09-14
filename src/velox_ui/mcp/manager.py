"""MCP server lifecycle, tool cache and the approval gate.

Connections are not kept open between requests. An MCP server configured here is
typically a local subprocess (``stdio``) or an occasionally-reached HTTP endpoint, not
something worth pooling like the provider HTTP client: :meth:`McpManager.connect`
opens a connection just long enough to list tools and persist the cache, and
:meth:`McpManager.call_tool` opens one just long enough to make the one call a turn
needs. This trades a little latency per tool call (a subprocess spawn, or a TCP
connect) for never leaking a subprocess or a stale session — acceptable, because tool
calls happen inside an already-multi-second model turn, not on the TTFT-critical path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

import msgspec

from velox_ui.db.repositories.mcp_servers import McpServerRepository
from velox_ui.errors import NotFoundError, ValidationError
from velox_ui.mcp.client import McpClient, McpTool, McpToolResult
from velox_ui.mcp.http_sse import HttpSseMcpClient
from velox_ui.mcp.stdio import StdioMcpClient

if TYPE_CHECKING:
    from velox_ui.state import AppState

__all__ = [
    "ApprovalDecision",
    "McpManager",
    "PendingApproval",
    "ServerTool",
    "build_client",
]

_log = logging.getLogger("velox.mcp.manager")

ApprovalMode = Literal["always", "once", "never"]


class ApprovalDecision(msgspec.Struct, frozen=True):
    """What the approval gate decided for one tool call."""

    approved: bool
    requires_wait: bool
    """Whether the caller must wait on a pending approval before executing."""


class ServerTool(msgspec.Struct, frozen=True):
    """A cached tool paired with just enough of its owning server to use it later.

    Detached from the ORM row deliberately: this outlives the session that fetched
    it (it is built during ``ChatService.prepare`` and read again mid-stream, long
    after that session has closed), so it carries plain values rather than an
    :class:`~velox_ui.db.models.McpServer` instance whose unloaded attributes would
    raise on access once detached.
    """

    server_id: str
    server_name: str
    approval: ApprovalMode
    tool: McpTool


class _ServerRecord(msgspec.Struct, frozen=True):
    """A server's fields, read out of its ORM row while the session is still open.

    ``db.session()`` rolls back on exit (a deliberate read-only guarantee), and
    SQLAlchemy expires every attribute of every object touched by a transaction that
    rolls back — so an :class:`~velox_ui.db.models.McpServer` instance handed back
    after that raises ``DetachedInstanceError`` on the next attribute read, not just
    on a lazy relationship. Every method below reads what it needs into one of these
    before its session closes, exactly once, rather than holding the ORM row.
    """

    id: str
    owner_id: str | None
    name: str
    transport: str
    config: dict[str, Any]
    auth_ref: str | None
    approval: ApprovalMode
    tool_cache: list[dict[str, Any]]


class PendingApproval:
    """One tool call awaiting a human decision via ``POST /api/tools/approve``."""

    __slots__ = ("call_id", "future", "server_id", "tool_name")

    def __init__(self, call_id: str, *, server_id: str, tool_name: str) -> None:
        """Create a pending approval with its own resolvable future."""
        self.call_id = call_id
        self.server_id = server_id
        self.tool_name = tool_name
        self.future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()


class McpManager:
    """Server connect/tool-cache/approval operations, bound to one :class:`AppState`.

    Args:
        state: Application state, for the database and the secret box.
    """

    __slots__ = ("_approved_once", "_pending", "_state")

    def __init__(self, state: AppState) -> None:
        """Bind the manager to application state."""
        self._state = state
        self._pending: dict[str, PendingApproval] = {}
        self._approved_once: set[tuple[str, str]] = set()

    async def connect(self, server_id: str, *, user_id: str) -> list[McpTool]:
        """Connect to a server, list its tools and persist the cache.

        Raises:
            NotFoundError: If the server does not exist or is not visible to the user.
            McpError: If the connection or handshake fails.
        """
        server = await self._get_visible(server_id, user_id=user_id)

        auth_token = await self._decrypt_auth(server)
        client = await build_client(server, auth_token=auth_token)
        try:
            await client.initialize()
            tools = await client.list_tools()
        finally:
            await client.close()

        async with self._state.db.write() as session:
            await McpServerRepository(session).set_tool_cache(
                server_id, [msgspec.structs.asdict(tool) for tool in tools]
            )
        return tools

    async def cached_tools(self, server_id: str, *, user_id: str) -> list[McpTool]:
        """Return the last cached tool list, without reconnecting."""
        server = await self._get_visible(server_id, user_id=user_id)
        return [McpTool(**entry) for entry in server.tool_cache]

    async def tools_for_servers(
        self, server_ids: Sequence[str], *, user_id: str
    ) -> list[ServerTool]:
        """Return the cached tools for a set of enabled servers, ready to outlive a session.

        Used to build a turn's offered tool list from ``custom_model.tools``. A server
        that is disabled, missing, or not visible to the caller contributes nothing
        rather than failing the whole turn — the same tolerance
        :meth:`~velox_ui.services.chat.ChatService._capabilities_of` applies to a
        capability probe that cannot answer.
        """
        found: list[ServerTool] = []
        async with self._state.db.session() as session:
            repository = McpServerRepository(session)
            for server_id in server_ids:
                row = await repository.get(server_id)
                if row is None or not row.enabled:
                    continue
                if row.owner_id is not None and row.owner_id != user_id:
                    continue
                for entry in row.tool_cache or []:
                    found.append(
                        ServerTool(
                            server_id=row.id,
                            server_name=row.name,
                            approval=row.approval,  # type: ignore[arg-type]
                            tool=McpTool(**entry),
                        )
                    )
        return found

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any], *, user_id: str
    ) -> McpToolResult:
        """Execute one tool call against a live connection, opened for this call only."""
        server = await self._get_visible(server_id, user_id=user_id)
        auth_token = await self._decrypt_auth(server)
        client = await build_client(server, auth_token=auth_token)
        try:
            await client.initialize()
            return await client.call_tool(tool_name, arguments)
        finally:
            await client.close()

    def gate(
        self, *, server_id: str, approval: ApprovalMode, tool_name: str
    ) -> ApprovalDecision:
        """Decide whether a call may run immediately or needs a person's approval.

        Args:
            server_id: The owning server's id, for the ``"once"`` per-tool memory.
            approval: The server's approval mode.
            tool_name: The tool being called.
        """
        if approval == "never":
            return ApprovalDecision(approved=True, requires_wait=False)
        if approval == "once" and (server_id, tool_name) in self._approved_once:
            return ApprovalDecision(approved=True, requires_wait=False)
        return ApprovalDecision(approved=False, requires_wait=True)

    def register_pending(
        self, call_id: str, *, server_id: str, tool_name: str
    ) -> PendingApproval:
        """Register a call awaiting approval, keyed by its own SSE ``tool_call`` id.

        Using the same id the client already saw in the ``tool_call`` event (rather
        than minting a new one) is what lets ``POST /api/tools/approve`` name the
        right call without an extra round trip to learn an approval-specific id.
        """
        pending = PendingApproval(call_id, server_id=server_id, tool_name=tool_name)
        self._pending[call_id] = pending
        return pending

    async def wait_for_approval(self, call_id: str, *, timeout_s: float = 300.0) -> bool:
        """Block until a pending call is approved, rejected, or the wait times out."""
        pending = self._pending.get(call_id)
        if pending is None:
            raise NotFoundError("No such pending tool call.")
        try:
            return await asyncio.wait_for(pending.future, timeout=timeout_s)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(call_id, None)

    async def _get_visible(self, server_id: str, *, user_id: str) -> _ServerRecord:
        """Fetch one server, converted to a detached-safe record, or raise 404."""
        async with self._state.db.session() as session:
            row = await McpServerRepository(session).get(server_id)
            if row is None or (row.owner_id is not None and row.owner_id != user_id):
                raise NotFoundError("No such MCP server.")
            return _ServerRecord(
                id=row.id,
                owner_id=row.owner_id,
                name=row.name,
                transport=row.transport,
                config=dict(row.config or {}),
                auth_ref=row.auth_ref,
                approval=row.approval,  # type: ignore[arg-type]
                tool_cache=list(row.tool_cache or []),
            )

    async def _decrypt_auth(self, server: _ServerRecord) -> str | None:
        """Decrypt a server's stored credential, if it has one."""
        if not server.auth_ref:
            return None
        async with self._state.db.session() as session:
            secret = await McpServerRepository(session).get_secret(server.auth_ref)
            if secret is None:
                return None
            return self._state.secrets.decrypt(secret.nonce, secret.ciphertext, ref=secret.ref)

    def resolve(self, call_id: str, *, approved: bool, remember: bool = False) -> bool:
        """Resolve a pending approval from ``POST /api/tools/approve``.

        Returns:
            Whether a pending call with this id was found.
        """
        pending = self._pending.get(call_id)
        if pending is None or pending.future.done():
            return False
        if approved and remember:
            self._approved_once.add((pending.server_id, pending.tool_name))
        pending.future.set_result(approved)
        return True


async def build_client(server: _ServerRecord, *, auth_token: str | None) -> McpClient:
    """Construct a transport client from a stored server's config.

    Raises:
        ValidationError: If ``transport`` names anything other than ``stdio`` or
            ``http_sse``, or the config is missing a required field.
    """
    config = server.config or {}
    if server.transport == "stdio":
        command = config.get("command")
        if not command:
            raise ValidationError("stdio MCP server config is missing 'command'.")
        env = dict(config.get("env", {}))
        if auth_token is not None:
            env[config.get("auth_env", "MCP_AUTH_TOKEN")] = auth_token
        return StdioMcpClient(command=command, args=list(config.get("args", [])), env=env)
    if server.transport == "http_sse":
        url = config.get("url")
        if not url:
            raise ValidationError("http_sse MCP server config is missing 'url'.")
        headers = dict(config.get("headers", {}))
        if auth_token is not None:
            headers["Authorization"] = f"Bearer {auth_token}"
        return HttpSseMcpClient(url=url, headers=headers)
    raise ValidationError(f"Unknown MCP transport '{server.transport}'.")
