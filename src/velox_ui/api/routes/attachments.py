"""Image attachments for vision models.

Cold path: an upload happens before a turn is sent, never inside one. The bytes are
stored like any other upload (``security/uploads.py``) and the turn refers to them by
file id, so the message row holds a reference and the image is read from disk only when
a request to a provider is built (``services/chat.py``).
"""

from __future__ import annotations

from typing import Annotated, Any

import msgspec
from fastapi import APIRouter, UploadFile
from fastapi import File as FastApiFile
from pydantic import BaseModel

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.repositories.files import FileRepository
from velox_ui.db.repositories.files import to_summary as file_to_summary
from velox_ui.security.uploads import store_upload, validate_image_upload

router = APIRouter(tags=["attachments"])


class AttachmentResponse(BaseModel):
    """An uploaded image's metadata. Its bytes are at ``/api/files/{id}/content``."""

    id: str
    filename: str
    content_type: str
    size_bytes: int


@router.post(
    "/api/attachments",
    response_model=AttachmentResponse,
    status_code=201,
    summary="Upload an image to attach to a message",
)
async def upload_attachment(
    principal: CurrentPrincipal,
    state: State,
    upload: Annotated[UploadFile, FastApiFile()],
) -> AttachmentResponse:
    """Upload a PNG, JPEG, GIF or WebP image, sniffed and size-capped."""
    data = await upload.read()
    content = validate_image_upload(data)
    storage_key = store_upload(state.settings.data_dir, content)
    async with state.db.write() as session:
        repo = FileRepository(session)
        file = await repo.by_hash(user_id=principal.user_id, sha256=content.sha256)
        if file is None:
            file = await repo.create(
                user_id=principal.user_id,
                filename=(upload.filename or "image")[:200],
                content_type=content.content_type,
                size_bytes=len(content.data),
                sha256=content.sha256,
                storage_key=storage_key,
            )
            await session.flush()
        summary: dict[str, Any] = msgspec.structs.asdict(file_to_summary(file))
    return AttachmentResponse(
        id=summary["id"],
        filename=summary["filename"],
        content_type=summary["content_type"],
        size_bytes=summary["size_bytes"],
    )
