"""Image generation (ADR-0014, ADR-0021).

A management-style, synchronous action, not part of a completion turn: a request
blocks for the few seconds a generation call takes and returns the result directly,
the same way an MCP ``connect`` call blocks rather than going through a job (unlike
the hour-long downloads ADR-0017 built a job registry for).
"""

from __future__ import annotations

import base64

from fastapi import APIRouter
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.repositories.files import FileRepository
from velox_ui.plugins.errors import PluginDisabledError
from velox_ui.security.uploads import UploadedContent, store_upload

router = APIRouter(prefix="/api/images", tags=["plugins"])


class GenerateImagesRequest(BaseModel):
    """Payload for an image-generation request."""

    prompt: str = Field(min_length=1, max_length=4000)
    size: str | None = None
    n: int = Field(default=1, ge=1, le=4)


class GeneratedImageResponse(BaseModel):
    """One generated image, already stored as a file."""

    file_id: str
    content_type: str


class GenerateImagesResponse(BaseModel):
    """The generated images."""

    images: list[GeneratedImageResponse]


@router.post("/generate", response_model=GenerateImagesResponse, summary="Generate an image")
async def generate_images(
    payload: GenerateImagesRequest, principal: CurrentPrincipal, state: State
) -> GenerateImagesResponse:
    """Generate one or more images and store each as a file the caller owns.

    Raises:
        PluginDisabledError: If the images plugin is not enabled and configured.
        PluginUpstreamError: If the configured backend fails to answer.
    """
    plugin = await state.plugins.get("images")
    if plugin is None:
        raise PluginDisabledError("Image generation is not enabled. Configure it in settings.")

    generated = await plugin.generate(prompt=payload.prompt, size=payload.size, n=payload.n)

    images: list[GeneratedImageResponse] = []
    async with state.db.write() as session:
        repository = FileRepository(session)
        for item in generated:
            data = base64.b64decode(item.b64)
            content = UploadedContent(data, item.content_type)
            storage_key = store_upload(state.settings.data_dir, content)
            file = await repository.create(
                user_id=principal.user_id,
                filename="generated-image.png",
                content_type=content.content_type,
                size_bytes=len(content.data),
                sha256=content.sha256,
                storage_key=storage_key,
            )
            await session.flush()
            images.append(
                GeneratedImageResponse(file_id=file.id, content_type=file.content_type)
            )
    return GenerateImagesResponse(images=images)
