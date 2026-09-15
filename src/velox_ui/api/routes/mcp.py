"""MCP server CRUD, connect and tool listing.

Cold path throughout: adding a server, connecting to refresh its tool cache, and
listing its tools are management actions, not part of a completion turn — the turn
itself reads the persisted cache (``services/chat.py``), never talking to the MCP
server directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.models import McpServer
from velox_ui.db.repositories.mcp_servers import McpServerRepository
from velox_ui.errors import ForbiddenError, NotFoundError, ValidationError
from velox_ui.security.crypto import mask_secret
from velox_ui.services.mcp_import import McpImportError, parse_claude_mcp_config

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])

_TRANSPORTS = ("stdio", "http_sse")
_APPROVALS = ("always", "once", "never")


class McpServerResponse(BaseModel):
    """A configured MCP server, credentials masked."""

    id: str
    owner_id: str | None
    name: str
    transport: str
    config: dict[str, Any]
    auth_hint: str | None
    enabled: bool
    approval: str
    tool_count: int
    created_at: int


class CreateMcpServerRequest(BaseModel):
    """Payload for adding an MCP server."""

    name: str = Field(min_length=1, max_length=200)
    transport: str
    config: dict[str, Any] = Field(default_factory=dict)
    auth_token: str | None = None
    approval: str = "always"
    enabled: bool = True

    @field_validator("transport")
    @classmethod
    def _valid_transport(cls, value: str) -> str:
        if value not in _TRANSPORTS:
            raise ValueError(f"transport must be one of {_TRANSPORTS}")
        return value

    @field_validator("approval")
    @classmethod
    def _valid_approval(cls, value: str) -> str:
        if value not in _APPROVALS:
            raise ValueError(f"approval must be one of {_APPROVALS}")
        return value


class UpdateMcpServerRequest(BaseModel):
    """Payload for updating an MCP server. Every field is optional.

    ``auth_token``: ``None`` leaves the stored credential untouched, an empty string
    removes it, anything else replaces it — the same convention
    ``ProviderPatch.api_key`` uses.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    auth_token: str | None = None
    enabled: bool | None = None
    approval: str | None = None

    @field_validator("approval")
    @classmethod
    def _valid_approval(cls, value: str | None) -> str | None:
        if value is not None and value not in _APPROVALS:
            raise ValueError(f"approval must be one of {_APPROVALS}")
        return value


class McpToolResponse(BaseModel):
    """One tool as offered by a server."""

    name: str
    description: str
    input_schema: dict[str, Any]


def _to_response(server: McpServer) -> McpServerResponse:
    hint = None
    if server.auth_ref:
        hint = "set"  # the real hint lives on the `secret` row; not fetched on the list path
    return McpServerResponse(
        id=server.id,
        owner_id=server.owner_id,
        name=server.name,
        transport=server.transport,
        config=dict(server.config or {}),
        auth_hint=hint,
        enabled=bool(server.enabled),
        approval=server.approval,
        tool_count=len(server.tool_cache or []),
        created_at=server.created_at,
    )


def _owned_or_404(server: McpServer | None, *, user_id: str) -> McpServer:
    if server is None:
        raise NotFoundError("No such MCP server.")
    if server.owner_id is not None and server.owner_id != user_id:
        raise ForbiddenError("This MCP server belongs to someone else.")
    return server


@router.get("", response_model=list[McpServerResponse], summary="List MCP servers")
async def list_servers(principal: CurrentPrincipal, state: State) -> list[McpServerResponse]:
    """Return the caller's own servers plus any instance-wide (ownerless) ones."""
    async with state.db.session() as session:
        servers = await McpServerRepository(session).list_visible(user_id=principal.user_id)
        return [_to_response(s) for s in servers]


@router.post("", response_model=McpServerResponse, status_code=201, summary="Add an MCP server")
async def create_server(
    payload: CreateMcpServerRequest, principal: CurrentPrincipal, state: State
) -> McpServerResponse:
    """Add an MCP server. A bearer token or env credential is encrypted at rest."""
    async with state.db.write() as session:
        repository = McpServerRepository(session)
        server = await repository.create(
            owner_id=principal.user_id,
            name=payload.name,
            transport=payload.transport,
            config=payload.config,
            approval=payload.approval,
            enabled=payload.enabled,
        )
        await session.flush()
        if payload.auth_token:
            nonce, ciphertext = state.secrets.encrypt(payload.auth_token, ref=server.id)
            await repository.put_secret(
                server.id,
                nonce=nonce,
                ciphertext=ciphertext,
                hint=mask_secret(payload.auth_token),
            )
            server.auth_ref = server.id
        return _to_response(server)


class ImportMcpServersRequest(BaseModel):
    """A pasted or uploaded Claude Code ``mcpServers`` config."""

    raw: str = Field(min_length=1)


class ImportMcpServersResponse(BaseModel):
    """The servers created, and any entries whose ``cwd`` could not be carried over."""

    servers: list[McpServerResponse]
    dropped_cwd: list[str]


@router.post(
    "/import",
    response_model=ImportMcpServersResponse,
    status_code=201,
    summary="Import servers from a Claude Code mcpServers config",
)
async def import_servers(
    payload: ImportMcpServersRequest, principal: CurrentPrincipal, state: State
) -> ImportMcpServersResponse:
    """Translate and create servers from Claude Code's own config shape.

    Accepts a full ``{"mcpServers": {...}}`` file, a bare name-keyed map, or a
    single server object (see ``services/mcp_import.py``). Credentials found in
    ``env``/``headers`` are carried over as plain config, exactly as the source had
    them — encrypting one requires knowing which key is a secret, so that step is
    left to editing the server afterward.

    Raises:
        ValidationError: If the payload isn't a config shape this importer
            recognises.
    """
    try:
        imported = parse_claude_mcp_config(payload.raw)
    except McpImportError as exc:
        raise ValidationError(str(exc)) from exc

    created: list[McpServerResponse] = []
    dropped_cwd: list[str] = []
    async with state.db.write() as session:
        repository = McpServerRepository(session)
        for server in imported:
            row = await repository.create(
                owner_id=principal.user_id,
                name=server.name,
                transport=server.transport,
                config=server.config,
                approval="always",
                enabled=True,
            )
            await session.flush()
            created.append(_to_response(row))
            if server.dropped_cwd:
                dropped_cwd.append(server.name)
    return ImportMcpServersResponse(servers=created, dropped_cwd=dropped_cwd)


@router.get("/{server_id}", response_model=McpServerResponse, summary="Get an MCP server")
async def get_server(
    server_id: str, principal: CurrentPrincipal, state: State
) -> McpServerResponse:
    """Return one server.

    Raises:
        NotFoundError: If it does not exist.
        ForbiddenError: If it belongs to someone else.
    """
    async with state.db.session() as session:
        server = await McpServerRepository(session).get(server_id)
        return _to_response(_owned_or_404(server, user_id=principal.user_id))


@router.patch("/{server_id}", response_model=McpServerResponse, summary="Update an MCP server")
async def update_server(
    server_id: str, payload: UpdateMcpServerRequest, principal: CurrentPrincipal, state: State
) -> McpServerResponse:
    """Update a server owned by the caller.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    async with state.db.write() as session:
        repository = McpServerRepository(session)
        updated = await repository.update(
            server_id,
            owner_id=principal.user_id,
            name=payload.name,
            config=payload.config,
            enabled=payload.enabled,
            approval=payload.approval,
        )
        if not updated:
            raise NotFoundError("No such MCP server.")
        server = await repository.get(server_id)
        assert server is not None  # noqa: S101 - just updated in this transaction

        if payload.auth_token is not None:
            if payload.auth_token == "":
                if server.auth_ref:
                    await repository.delete_secret(server.auth_ref)
                server.auth_ref = None
            else:
                ref = server.auth_ref or server_id
                nonce, ciphertext = state.secrets.encrypt(payload.auth_token, ref=ref)
                await repository.put_secret(
                    ref,
                    nonce=nonce,
                    ciphertext=ciphertext,
                    hint=mask_secret(payload.auth_token),
                )
                server.auth_ref = ref
        return _to_response(server)


@router.delete("/{server_id}", status_code=204, summary="Delete an MCP server")
async def delete_server(server_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Delete a server owned by the caller, including its stored credential.

    Raises:
        NotFoundError: If it does not exist, or is not the caller's.
    """
    async with state.db.write() as session:
        removed = await McpServerRepository(session).delete(
            server_id, owner_id=principal.user_id
        )
    if not removed:
        raise NotFoundError("No such MCP server.")


@router.post(
    "/{server_id}/connect",
    response_model=list[McpToolResponse],
    summary="Connect to a server and refresh its tool cache",
)
async def connect_server(
    server_id: str, principal: CurrentPrincipal, state: State
) -> list[McpToolResponse]:
    """Connect to the server, list its tools and persist the cache.

    Raises:
        NotFoundError: If it does not exist, or is not visible to the caller.
        McpError: If the connection or handshake fails.
    """
    tools = await state.mcp.connect(server_id, user_id=principal.user_id)
    return [
        McpToolResponse(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in tools
    ]


@router.get(
    "/{server_id}/tools",
    response_model=list[McpToolResponse],
    summary="List a server's cached tools",
)
async def list_server_tools(
    server_id: str, principal: CurrentPrincipal, state: State
) -> list[McpToolResponse]:
    """Return the last cached tool list, without reconnecting."""
    tools = await state.mcp.cached_tools(server_id, user_id=principal.user_id)
    return [
        McpToolResponse(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in tools
    ]
