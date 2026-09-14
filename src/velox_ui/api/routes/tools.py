"""The caller's available tools, and resolving a pending approval.

``GET /api/tools`` is MCP-only for phase 8: no builtin tool (calculator, code
execution, HTTP fetch) is implemented (ADR-0014's plugin entry-point mechanism is the
documented path for adding one later), so the catalogue this endpoint returns is
exactly the union of the caller's enabled MCP servers' cached tools, qualified by
server name the same way :mod:`velox_ui.mcp.schema_translate` qualifies them for a
provider request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.repositories.mcp_servers import McpServerRepository

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolResponse(BaseModel):
    """One tool available to the caller, from any source."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_id: str
    server_name: str


class ApproveToolCallRequest(BaseModel):
    """Resolve a pending tool call.

    Answers the ``event: tool_call`` raised mid-stream with ``approval: "required"``.
    """

    call_id: str = Field(min_length=1)
    approved: bool
    remember: bool = False


class ApproveToolCallResponse(BaseModel):
    """Whether a pending call was found and resolved."""

    ok: bool


@router.get("", response_model=list[ToolResponse], summary="List available tools")
async def list_tools(principal: CurrentPrincipal, state: State) -> list[ToolResponse]:
    """Return every tool the caller's enabled MCP servers currently offer, cached."""
    async with state.db.session() as session:
        servers = await McpServerRepository(session).list_visible(user_id=principal.user_id)
        return [
            ToolResponse(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", tool.get("input_schema", {})),
                server_id=server.id,
                server_name=server.name,
            )
            for server in servers
            if server.enabled
            for tool in (server.tool_cache or [])
        ]


@router.post(
    "/approve", response_model=ApproveToolCallResponse, summary="Approve or reject a tool call"
)
async def approve_tool_call(
    payload: ApproveToolCallRequest, principal: CurrentPrincipal, state: State
) -> ApproveToolCallResponse:
    """Resolve a pending tool call that is blocking a turn's stream.

    The turn is waiting on ``McpManager.wait_for_approval`` for this ``call_id``; this
    just wakes it up with the caller's decision. A ``call_id`` that is not (or is no
    longer) pending resolves to ``ok: false`` rather than an error, since the stream
    may already have timed the wait out or the client may be retrying a click.
    """
    del principal
    resolved = state.mcp.resolve(
        payload.call_id, approved=payload.approved, remember=payload.remember
    )
    return ApproveToolCallResponse(ok=resolved)
