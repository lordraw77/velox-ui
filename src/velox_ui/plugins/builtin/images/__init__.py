"""Builtin image-generation plugin: an OpenAI-compatible HTTP client (ADR-0021).

Registered under the ``velox_ui.images`` entry-point group in this repository's own
``pyproject.toml``, so the default (disabled) path exercises real plugin discovery.
"""

from __future__ import annotations

import httpx

from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import GeneratedImage, PluginConfig, PluginValidation
from velox_ui.security.crypto import SecretBox

__all__ = ["OpenAICompatImagePlugin"]

_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class OpenAICompatImagePlugin:
    """Calls ``POST {base_url}/v1/images/generations`` on a configured host."""

    name = "openai-compat-images"

    def __init__(self, config: PluginConfig, *, secrets: SecretBox) -> None:
        """Store the configuration; the HTTP client is built lazily on first call."""
        del secrets  # the plaintext key already arrives decrypted on `config.api_key`
        self._config = config

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def generate(
        self, *, prompt: str, size: str | None = None, n: int = 1
    ) -> list[GeneratedImage]:
        """Generate one or more images from a prompt.

        Raises:
            PluginUpstreamError: If the configured backend cannot be reached or
                returns a non-2xx response.
        """
        base_url = (self._config.base_url or "").rstrip("/")
        body: dict[str, object] = {"prompt": prompt, "n": n}
        if self._config.model:
            body["model"] = self._config.model
        if size:
            body["size"] = size

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{base_url}/v1/images/generations", json=body, headers=self._headers()
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise PluginUpstreamError(
                    f"Image generation backend at {base_url!r} did not answer: {exc}"
                ) from exc

        try:
            payload = response.json()
            items = payload["data"]
            return [
                GeneratedImage(b64=item["b64_json"], content_type="image/png") for item in items
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginUpstreamError(
                f"Image generation backend at {base_url!r} returned an unexpected response."
            ) from exc

    async def validate(self) -> PluginValidation:
        """Probe the configured host, tolerating any failure rather than raising."""
        base_url = (self._config.base_url or "").rstrip("/")
        if not base_url:
            return PluginValidation(ok=False, detail="No base_url configured.")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(f"{base_url}/v1/models", headers=self._headers())
            if response.is_success:
                return PluginValidation(ok=True, detail="Backend reachable.")
            return PluginValidation(
                ok=False, detail=f"Backend responded with HTTP {response.status_code}."
            )
        except httpx.HTTPError as exc:
            return PluginValidation(ok=False, detail=f"Backend unreachable: {exc}")
