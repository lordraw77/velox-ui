"""Tag CRUD and chat attachment, for organizing the chat sidebar.

Cold path: like ``folders.py``, this uses Pydantic.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.models import Tag
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.tags import TagRepository, TagSummary
from velox_ui.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/api/tags", tags=["tags"])


class CreateTagRequest(BaseModel):
    """Payload for creating a tag."""

    name: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=16)


class UpdateTagRequest(BaseModel):
    """Payload for renaming a tag or changing its color."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=16)


class TagResponse(BaseModel):
    """A tag, as the UI renders it."""

    id: str
    name: str
    color: str | None


def _to_response(tag: Tag | TagSummary) -> TagResponse:
    """Build a response from an ORM ``Tag`` row or a :class:`TagSummary`."""
    return TagResponse(id=tag.id, name=tag.name, color=tag.color)


@router.get("", response_model=list[TagResponse], summary="List tags")
async def list_tags(principal: CurrentPrincipal, state: State) -> list[TagResponse]:
    """Return every tag owned by the caller, alphabetically."""
    async with state.db.session() as session:
        tags = await TagRepository(session).list_all(user_id=principal.user_id)
    return [_to_response(tag) for tag in tags]


@router.post("", response_model=TagResponse, status_code=201, summary="Create a tag")
async def create_tag(
    payload: CreateTagRequest, principal: CurrentPrincipal, state: State
) -> TagResponse:
    """Create a tag.

    Raises:
        ConflictError: If the caller already has a tag with this name.
    """
    async with state.db.write() as session:
        repository = TagRepository(session)
        existing = await repository.list_all(user_id=principal.user_id)
        if any(tag.name == payload.name for tag in existing):
            raise ConflictError("A tag with this name already exists.")
        tag = await repository.create(
            user_id=principal.user_id, name=payload.name, color=payload.color
        )
        await session.flush()
        return _to_response(tag)


@router.patch("/{tag_id}", response_model=TagResponse, summary="Update a tag")
async def update_tag(
    tag_id: str, payload: UpdateTagRequest, principal: CurrentPrincipal, state: State
) -> TagResponse:
    """Rename a tag and/or change its color.

    Raises:
        NotFoundError: If it is not the caller's, or does not exist.
    """
    async with state.db.write() as session:
        repository = TagRepository(session)
        updated = await repository.update(
            tag_id, user_id=principal.user_id, name=payload.name, color=payload.color
        )
        if not updated:
            raise NotFoundError("No such tag.")
        tag = await repository.get(tag_id, user_id=principal.user_id)
        assert tag is not None  # noqa: S101 - just updated in this transaction
        return _to_response(tag)


@router.delete("/{tag_id}", status_code=204, summary="Delete a tag")
async def delete_tag(tag_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Delete a tag. Attachments cascade.

    Raises:
        NotFoundError: If it is not the caller's, or does not exist.
    """
    async with state.db.write() as session:
        removed = await TagRepository(session).delete(tag_id, user_id=principal.user_id)
    if not removed:
        raise NotFoundError("No such tag.")


@router.put("/{tag_id}/chats/{chat_id}", status_code=204, summary="Attach a tag to a chat")
async def attach_tag(
    tag_id: str, chat_id: str, principal: CurrentPrincipal, state: State
) -> None:
    """Attach a tag to a chat, both owned by the caller. Idempotent.

    Raises:
        NotFoundError: If the chat or the tag is not the caller's, or does not exist.
    """
    async with state.db.write() as session:
        attached = await TagRepository(session).attach(
            chat_id=chat_id, tag_id=tag_id, user_id=principal.user_id
        )
    if not attached:
        raise NotFoundError("No such chat or tag.")


@router.delete("/{tag_id}/chats/{chat_id}", status_code=204, summary="Detach a tag from a chat")
async def detach_tag(
    tag_id: str, chat_id: str, principal: CurrentPrincipal, state: State
) -> None:
    """Detach a tag from a chat.

    Raises:
        NotFoundError: If the attachment does not exist, or either side is not the
            caller's.
    """
    async with state.db.write() as session:
        removed = await TagRepository(session).detach(
            chat_id=chat_id, tag_id=tag_id, user_id=principal.user_id
        )
    if not removed:
        raise NotFoundError("No such attachment.")


@router.get("/{tag_id}/chats", response_model=list[str], summary="List chats carrying a tag")
async def chats_for_tag(tag_id: str, principal: CurrentPrincipal, state: State) -> list[str]:
    """Return the ids of the caller's chats carrying one tag."""
    async with state.db.session() as session:
        return list(
            await TagRepository(session).chat_ids_for_tag(tag_id, user_id=principal.user_id)
        )


@router.get(
    "/for-chat/{chat_id}", response_model=list[TagResponse], summary="List tags on a chat"
)
async def tags_for_chat(
    chat_id: str, principal: CurrentPrincipal, state: State
) -> list[TagResponse]:
    """Return the tags attached to one chat.

    Raises:
        NotFoundError: If the chat is not the caller's, or does not exist.
    """
    async with state.db.session() as session:
        if await ChatRepository(session).get(chat_id, user_id=principal.user_id) is None:
            raise NotFoundError("No such conversation.")
        tags = await TagRepository(session).for_chat(chat_id)
    return [_to_response(tag) for tag in tags]
