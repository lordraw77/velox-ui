"""Folder CRUD for the chat sidebar.

Cold path: folders change rarely, so this uses Pydantic like the other management
routes (``apikeys.py``, ``auth.py``), not msgspec.
"""

from __future__ import annotations

import msgspec
from fastapi import APIRouter
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.models import Folder
from velox_ui.db.repositories.folders import FolderRepository
from velox_ui.errors import NotFoundError

router = APIRouter(prefix="/api/folders", tags=["folders"])


class CreateFolderRequest(BaseModel):
    """Payload for creating a folder."""

    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    sort_order: int = 0


class RenameFolderRequest(BaseModel):
    """Payload for renaming a folder."""

    name: str = Field(min_length=1, max_length=200)


class MoveFolderRequest(BaseModel):
    """Payload for reparenting or reordering a folder."""

    parent_id: str | None = None
    sort_order: int | None = None


class FolderResponse(BaseModel):
    """A folder, as the sidebar renders it."""

    id: str
    parent_id: str | None
    name: str
    sort_order: int
    created_at: int


@router.get("", response_model=list[FolderResponse], summary="List folders")
async def list_folders(principal: CurrentPrincipal, state: State) -> list[FolderResponse]:
    """Return every folder owned by the caller, flat.

    The client assembles the tree from ``parent_id``.
    """
    async with state.db.session() as session:
        folders = await FolderRepository(session).list_all(user_id=principal.user_id)
    return [FolderResponse(**msgspec.structs.asdict(folder)) for folder in folders]


@router.post("", response_model=FolderResponse, status_code=201, summary="Create a folder")
async def create_folder(
    payload: CreateFolderRequest, principal: CurrentPrincipal, state: State
) -> FolderResponse:
    """Create a folder, optionally nested under another one of the caller's folders.

    Raises:
        NotFoundError: If ``parent_id`` is set but is not one of the caller's folders.
    """
    async with state.db.write() as session:
        repository = FolderRepository(session)
        if (
            payload.parent_id is not None
            and await repository.get(payload.parent_id, user_id=principal.user_id) is None
        ):
            raise NotFoundError("No such parent folder.")
        folder = await repository.create(
            user_id=principal.user_id,
            name=payload.name,
            parent_id=payload.parent_id,
            sort_order=payload.sort_order,
        )
        await session.flush()
        return _to_response(folder)


@router.patch("/{folder_id}", response_model=FolderResponse, summary="Rename a folder")
async def rename_folder(
    folder_id: str, payload: RenameFolderRequest, principal: CurrentPrincipal, state: State
) -> FolderResponse:
    """Rename a folder.

    Raises:
        NotFoundError: If it is not the caller's, or does not exist.
    """
    async with state.db.write() as session:
        repository = FolderRepository(session)
        if not await repository.rename(folder_id, user_id=principal.user_id, name=payload.name):
            raise NotFoundError("No such folder.")
        folder = await repository.get(folder_id, user_id=principal.user_id)
        assert folder is not None  # noqa: S101 - just renamed in this transaction
        return _to_response(folder)


@router.put("/{folder_id}/move", response_model=FolderResponse, summary="Move a folder")
async def move_folder(
    folder_id: str, payload: MoveFolderRequest, principal: CurrentPrincipal, state: State
) -> FolderResponse:
    """Reparent and/or reorder a folder.

    Raises:
        NotFoundError: If it is not the caller's, or does not exist. If ``parent_id``
            is set, it must also be one of the caller's folders.
    """
    async with state.db.write() as session:
        repository = FolderRepository(session)
        if (
            payload.parent_id is not None
            and await repository.get(payload.parent_id, user_id=principal.user_id) is None
        ):
            raise NotFoundError("No such parent folder.")
        moved = await repository.move(
            folder_id,
            user_id=principal.user_id,
            parent_id=payload.parent_id,
            sort_order=payload.sort_order,
        )
        if not moved:
            raise NotFoundError("No such folder.")
        folder = await repository.get(folder_id, user_id=principal.user_id)
        assert folder is not None  # noqa: S101 - just moved in this transaction
        return _to_response(folder)


@router.delete("/{folder_id}", status_code=204, summary="Delete a folder")
async def delete_folder(folder_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Delete a folder.

    Child folders cascade; chats inside it are moved to the root, not deleted.

    Raises:
        NotFoundError: If it is not the caller's, or does not exist.
    """
    async with state.db.write() as session:
        removed = await FolderRepository(session).delete(folder_id, user_id=principal.user_id)
    if not removed:
        raise NotFoundError("No such folder.")


def _to_response(folder: Folder) -> FolderResponse:
    """Build a response from an ORM ``Folder`` row."""
    return FolderResponse(
        id=folder.id,
        parent_id=folder.parent_id,
        name=folder.name,
        sort_order=folder.sort_order,
        created_at=folder.created_at,
    )
