"""Conversations and the completion stream.

Every route here sits on the hot path, so none of them declares a ``response_model``
and none constructs a Pydantic model: bodies are decoded and encoded with msgspec
(ADR-0001). A test enforces that (``tests/integration/test_hot_path_boundary.py``).
"""

from __future__ import annotations

from typing import Annotated

import msgspec
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.api.msgspec_io import json_response, read_struct
from velox_ui.api.pagination import clamp_limit, decode_cursor, encode_cursor
from velox_ui.api.sse import SSE_HEADERS
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.errors import NotFoundError
from velox_ui.providers.base import SamplingParams
from velox_ui.services.chat import ChatService

router = APIRouter(prefix="/api/chats", tags=["chats"])


class CreateChat(msgspec.Struct):
    """Body for creating a conversation."""

    title: str = "New conversation"
    folder_id: str | None = None
    model_ref: str | None = None


class CompletionRequest(msgspec.Struct):
    """Body for running a turn."""

    content: str
    model_ref: str
    parent_id: str | None = None
    system_prompt: str | None = None
    params: SamplingParams = msgspec.field(default_factory=SamplingParams)


@router.post("", summary="Create a conversation")
async def create_chat(request: Request, principal: CurrentPrincipal, state: State) -> Response:
    """Create an empty conversation."""
    body = await read_struct(request, CreateChat)
    async with state.db.write() as session:
        chat = await ChatRepository(session).create(
            user_id=principal.user_id,
            title=body.title,
            folder_id=body.folder_id,
            model_ref=body.model_ref,
        )
        await session.flush()
        payload = {
            "id": chat.id,
            "title": chat.title,
            "folder_id": chat.folder_id,
            "model_ref": chat.model_ref,
            "created_at": chat.created_at,
        }
    return json_response(payload, status_code=201)


@router.get("", summary="List conversations")
async def list_chats(
    principal: CurrentPrincipal,
    state: State,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    archived: Annotated[bool, Query()] = False,
    folder: Annotated[str | None, Query()] = None,
) -> Response:
    """Return one keyset page of the sidebar (ADR-0007)."""
    decoded = decode_cursor(cursor, arity=3) if cursor else None
    async with state.db.session() as session:
        items, next_key = await ChatRepository(session).list_page(
            user_id=principal.user_id,
            limit=clamp_limit(limit),
            cursor=decoded,
            archived=archived,
            folder_id=folder,
        )
    return json_response(
        {
            "items": items,
            "next_cursor": encode_cursor(next_key) if next_key else None,
        }
    )


@router.get("/{chat_id}", summary="Open a conversation")
async def get_chat(
    chat_id: str,
    principal: CurrentPrincipal,
    state: State,
    branch: Annotated[str | None, Query()] = None,
) -> Response:
    """Return the conversation and the messages on its active branch.

    Args:
        chat_id: The conversation.
        principal: The caller.
        state: Application state.
        branch: Load a different branch tip instead of the stored active leaf.

    Raises:
        NotFoundError: If the conversation is not the caller's, or does not exist.
    """
    async with state.db.session() as session:
        repository = ChatRepository(session)
        chat = await repository.get(chat_id, user_id=principal.user_id)
        if chat is None:
            raise NotFoundError("No such conversation.")
        path = await repository.load_active_path(chat_id, leaf_id=branch or chat.active_leaf_id)
        payload = {
            "id": chat.id,
            "title": chat.title,
            "folder_id": chat.folder_id,
            "model_ref": chat.model_ref,
            "pinned": bool(chat.pinned),
            "archived": bool(chat.archived),
            "active_leaf_id": chat.active_leaf_id,
            "message_count": chat.message_count,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at,
            "messages": path,
        }
    return json_response(payload)


@router.delete("/{chat_id}", status_code=204, summary="Delete a conversation")
async def delete_chat(chat_id: str, principal: CurrentPrincipal, state: State) -> Response:
    """Soft-delete a conversation.

    Raises:
        NotFoundError: If it is not the caller's, or does not exist.
    """
    async with state.db.write() as session:
        removed = await ChatRepository(session).soft_delete(chat_id, user_id=principal.user_id)
    if not removed:
        raise NotFoundError("No such conversation.")
    return Response(status_code=204)


@router.post("/{chat_id}/completions", summary="Run a turn and stream the reply")
async def completions(
    chat_id: str, request: Request, principal: CurrentPrincipal, state: State
) -> StreamingResponse:
    """Stream a completion as server-sent events.

    The response is a raw byte stream. It declares no ``response_model`` and builds no
    Pydantic object, because everything here runs inside the 15 ms time-to-first-token
    budget (ADR-0001, ADR-0004).

    The event protocol is documented in docs/design/03-http-api.md: ``start``,
    ``status``, ``delta``, ``reasoning``, ``tool_call``, ``usage``, ``error``, ``done``.
    """
    body = await read_struct(request, CompletionRequest)

    # Preconditions are checked here, before a single byte is written: once the
    # response has started there is no status code left to send, and a client would
    # receive a 200 carrying a failure.
    turn = await ChatService(state).prepare(
        chat_id=chat_id,
        user_id=principal.user_id,
        content=body.content,
        model_ref=body.model_ref,
        parent_id=body.parent_id,
        params=body.params,
        system_prompt=body.system_prompt,
    )
    return StreamingResponse(
        turn.stream(), media_type="text/event-stream", headers=dict(SSE_HEADERS)
    )
