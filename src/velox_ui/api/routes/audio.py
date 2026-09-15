"""Speech transcription and synthesis (ADR-0014, ADR-0021).

Both operations are short (single chat turn / voice message length) and synchronous,
the same call shape as ``/api/images/generate`` — no job registry involved.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, UploadFile
from fastapi import File as FastApiFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.plugins.errors import PluginDisabledError

router = APIRouter(prefix="/api/audio", tags=["plugins"])


class TranscribeResponse(BaseModel):
    """A transcribed audio clip."""

    text: str


class SpeakRequest(BaseModel):
    """Payload for a text-to-speech request."""

    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = None


@router.post(
    "/transcribe", response_model=TranscribeResponse, summary="Transcribe audio to text"
)
async def transcribe(
    principal: CurrentPrincipal,
    state: State,
    audio: Annotated[UploadFile, FastApiFile()],
    language: str | None = None,
) -> TranscribeResponse:
    """Transcribe an uploaded audio clip.

    Raises:
        PluginDisabledError: If the voice plugin is not enabled and configured.
        PluginUpstreamError: If the configured backend fails to answer.
    """
    del principal
    plugin = await state.plugins.get("voice")
    if plugin is None:
        raise PluginDisabledError(
            "Voice transcription is not enabled. Configure it in settings."
        )
    data = await audio.read()
    text = await plugin.transcribe(
        audio=data, content_type=audio.content_type or "audio/webm", language=language
    )
    return TranscribeResponse(text=text)


@router.post("/speech", summary="Synthesize speech from text")
async def speech(
    payload: SpeakRequest, principal: CurrentPrincipal, state: State
) -> StreamingResponse:
    """Synthesize speech for a piece of text.

    Raises:
        PluginDisabledError: If the voice plugin is not enabled and configured.
        PluginUpstreamError: If the configured backend fails to answer.
    """
    del principal
    plugin = await state.plugins.get("voice")
    if plugin is None:
        raise PluginDisabledError("Voice synthesis is not enabled. Configure it in settings.")
    result = await plugin.speak(text=payload.text, voice=payload.voice)
    return StreamingResponse(iter([result.data]), media_type=result.content_type)
