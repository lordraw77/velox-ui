"""Builtin voice plugin: STT and TTS over an OpenAI-compatible HTTP host (ADR-0021).

Registered under the ``velox_ui.voice`` entry-point group in this repository's own
``pyproject.toml``. One plugin class covers both transcription and speech, since both
are operationally one backend connection (ADR-0014).
"""

from __future__ import annotations

import httpx

from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import AudioResult, PluginConfig, PluginValidation
from velox_ui.security.crypto import SecretBox

__all__ = ["OpenAICompatVoicePlugin"]

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class OpenAICompatVoicePlugin:
    """Calls ``/v1/audio/transcriptions`` and ``/v1/audio/speech`` on a configured host."""

    name = "openai-compat-voice"

    def __init__(self, config: PluginConfig, *, secrets: SecretBox) -> None:
        """Store the configuration; the HTTP client is built lazily on first call."""
        del secrets  # the plaintext key already arrives decrypted on `config.api_key`
        self._config = config

    def _headers(self, *, json: bool) -> dict[str, str]:
        headers = {}
        if json:
            headers["Content-Type"] = "application/json"
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def transcribe(
        self, *, audio: bytes, content_type: str, language: str | None = None
    ) -> str:
        """Transcribe an audio clip to text.

        Raises:
            PluginUpstreamError: If the configured backend cannot be reached or
                returns a non-2xx response.
        """
        base_url = (self._config.base_url or "").rstrip("/")
        model = self._config.model or "whisper-1"
        data = {"model": model}
        if language:
            data["language"] = language
        files = {"file": ("audio", audio, content_type)}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{base_url}/v1/audio/transcriptions",
                    data=data,
                    files=files,
                    headers=self._headers(json=False),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise PluginUpstreamError(
                    f"Transcription backend at {base_url!r} did not answer: {exc}"
                ) from exc

        try:
            return str(response.json()["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginUpstreamError(
                f"Transcription backend at {base_url!r} returned an unexpected response."
            ) from exc

    async def speak(self, *, text: str, voice: str | None = None) -> AudioResult:
        """Synthesize speech for a piece of text.

        Raises:
            PluginUpstreamError: If the configured backend cannot be reached or
                returns a non-2xx response.
        """
        base_url = (self._config.base_url or "").rstrip("/")
        body: dict[str, object] = {
            "model": self._config.model or "tts-1",
            "input": text,
            "voice": voice or self._config.extra.get("tts_voice") or "alloy",
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{base_url}/v1/audio/speech", json=body, headers=self._headers(json=True)
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise PluginUpstreamError(
                    f"Speech backend at {base_url!r} did not answer: {exc}"
                ) from exc

        content_type = response.headers.get("content-type", "audio/mpeg")
        return AudioResult(data=response.content, content_type=content_type)

    async def validate(self) -> PluginValidation:
        """Probe the configured host, tolerating any failure rather than raising."""
        base_url = (self._config.base_url or "").rstrip("/")
        if not base_url:
            return PluginValidation(ok=False, detail="No base_url configured.")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(
                    f"{base_url}/v1/models", headers=self._headers(json=False)
                )
            if response.is_success:
                return PluginValidation(ok=True, detail="Backend reachable.")
            return PluginValidation(
                ok=False, detail=f"Backend responded with HTTP {response.status_code}."
            )
        except httpx.HTTPError as exc:
            return PluginValidation(ok=False, detail=f"Backend unreachable: {exc}")
