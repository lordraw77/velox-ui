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
    custom_model_id: str | None = None


class UpdateChat(msgspec.Struct):
    """Body for patching a conversation's sidebar-facing fields.

    Every field is optional; only the ones present in the JSON body are applied.
    """

    title: str | msgspec.UnsetType = msgspec.UNSET
    pinned: bool | msgspec.UnsetType = msgspec.UNSET
    archived: bool | msgspec.UnsetType = msgspec.UNSET
    folder_id: str | msgspec.UnsetType | None = msgspec.UNSET


class CompletionRequest(msgspec.Struct):
    """Body for running a turn."""

    content: str
    model_ref: str
    parent_id: str | None = None
    system_prompt: str | None = None
    params: SamplingParams = msgspec.field(default_factory=SamplingParams)
    knowledge_ids: tuple[str, ...] = ()


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
            custom_model_id=body.custom_model_id,
        )
        await session.flush()
        payload = {
            "id": chat.id,
            "title": chat.title,
            "folder_id": chat.folder_id,
            "model_ref": chat.model_ref,
            "custom_model_id": chat.custom_model_id,
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
    tag: Annotated[str | None, Query()] = None,
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
            tag_id=tag,
        )
    return json_response(
        {
            "items": items,
            "next_cursor": encode_cursor(next_key) if next_key else None,
        }
    )


MESSAGE_PAGE_DEFAULT = 60
"""Messages sent when a conversation is opened.

Several screens' worth: the client virtualises, so what matters is having enough to
fill the viewport and absorb a quick upward flick before the next page arrives.
"""

MESSAGE_PAGE_MAX = 200


@router.get("/{chat_id}", summary="Open a conversation")
async def get_chat(
    chat_id: str,
    principal: CurrentPrincipal,
    state: State,
    branch: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> Response:
    """Return the conversation and the newest page of its active branch.

    Older messages are fetched with ``GET /api/chats/{id}/messages`` and the
    ``messages_cursor`` returned here, so opening a conversation costs one page no
    matter how long it is (ADR-0007).

    Args:
        chat_id: The conversation.
        principal: The caller.
        state: Application state.
        branch: Load a different branch tip instead of the stored active leaf.
        limit: Page size.

    Raises:
        NotFoundError: If the conversation is not the caller's, or does not exist.
    """
    async with state.db.session() as session:
        repository = ChatRepository(session)
        chat = await repository.get(chat_id, user_id=principal.user_id)
        if chat is None:
            raise NotFoundError("No such conversation.")
        path, older = await repository.load_path_page(
            chat_id,
            start_id=branch or chat.active_leaf_id,
            limit=_message_limit(limit),
        )
        payload = {
            "id": chat.id,
            "title": chat.title,
            "folder_id": chat.folder_id,
            "model_ref": chat.model_ref,
            "custom_model_id": chat.custom_model_id,
            "pinned": bool(chat.pinned),
            "archived": bool(chat.archived),
            "active_leaf_id": chat.active_leaf_id,
            "message_count": chat.message_count,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at,
            "messages": path,
            "messages_cursor": encode_cursor((older,)) if older else None,
        }
    return json_response(payload)


@router.get("/{chat_id}/messages", summary="Page backwards through a conversation")
async def list_messages(
    chat_id: str,
    principal: CurrentPrincipal,
    state: State,
    cursor: Annotated[str | None, Query()] = None,
    branch: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> Response:
    """Return one page of the active branch, older than the cursor.

    Without a cursor this is the newest page, the same one ``GET /api/chats/{id}``
    embeds. Pages are in reading order, oldest first, so a client prepends them as
    they arrive.

    Raises:
        NotFoundError: If the conversation is not the caller's, or does not exist.
        ValidationError: If the cursor is malformed.
    """
    start_id = str(decode_cursor(cursor, arity=1)[0]) if cursor else None
    async with state.db.session() as session:
        repository = ChatRepository(session)
        chat = await repository.get(chat_id, user_id=principal.user_id)
        if chat is None:
            raise NotFoundError("No such conversation.")
        items, older = await repository.load_path_page(
            chat_id,
            start_id=start_id or branch or chat.active_leaf_id,
            limit=_message_limit(limit),
        )
    return json_response(
        {"items": items, "next_cursor": encode_cursor((older,)) if older else None}
    )


def _message_limit(limit: int | None) -> int:
    """Clamp a requested message page size."""
    if limit is None:
        return MESSAGE_PAGE_DEFAULT
    return max(1, min(limit, MESSAGE_PAGE_MAX))


@router.patch("/{chat_id}", summary="Rename, pin, archive or move a conversation")
async def update_chat(
    chat_id: str, request: Request, principal: CurrentPrincipal, state: State
) -> Response:
    """Patch a conversation's sidebar-facing fields.

    Every field in the body is optional; a field absent from the JSON stays
    untouched, and ``folder_id: null`` moves the conversation to the root (distinct
    from omitting it).

    Raises:
        NotFoundError: If it is not the caller's, or does not exist.
    """
    body = await read_struct(request, UpdateChat)
    async with state.db.write() as session:
        updated = await ChatRepository(session).set_organization(
            chat_id,
            user_id=principal.user_id,
            title=None if body.title is msgspec.UNSET else body.title,
            pinned=None if body.pinned is msgspec.UNSET else body.pinned,
            archived=None if body.archived is msgspec.UNSET else body.archived,
            folder_id=body.folder_id,
        )
        if not updated:
            raise NotFoundError("No such conversation.")
        chat = await ChatRepository(session).get(chat_id, user_id=principal.user_id)
        assert chat is not None  # noqa: S101 - just updated in this transaction
        payload = {
            "id": chat.id,
            "title": chat.title,
            "folder_id": chat.folder_id,
            "model_ref": chat.model_ref,
            "custom_model_id": chat.custom_model_id,
            "pinned": bool(chat.pinned),
            "archived": bool(chat.archived),
            "updated_at": chat.updated_at,
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
        knowledge_ids=body.knowledge_ids,
    )
    return StreamingResponse(
        turn.stream(), media_type="text/event-stream", headers=dict(SSE_HEADERS)
    )
