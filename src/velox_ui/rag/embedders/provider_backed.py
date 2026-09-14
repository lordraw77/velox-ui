"""An embedder that calls an existing provider's ``embed()``.

Lets a collection use Ollama's ``/api/embed``, llama.cpp, or a cloud OpenAI-compatible
preset instead of the local ONNX default — the same registry built in phases 2-5,
reused rather than duplicated (ADR-0010).
"""

from __future__ import annotations

from collections.abc import Sequence

from velox_ui.providers.base import Provider

__all__ = ["ProviderBackedEmbedder"]


class ProviderBackedEmbedder:
    """Wraps ``Provider.embed()`` behind the ``Embedder`` protocol.

    See :class:`~velox_ui.rag.embedders.base.Embedder`.
    """

    def __init__(self, provider: Provider, *, model: str, dim: int) -> None:
        """Bind to an already-resolved provider and model.

        Args:
            provider: The adapter to call. ``UnsupportedCapability`` propagates as-is
                if the backend or model cannot embed.
            model: The model key to embed with.
            dim: The vector width this model produces, known ahead of time (probed or
                configured when the collection was created) so the collection schema
                does not depend on making a call first.
        """
        self._provider = provider
        self._model = model
        self.dim = dim
        self.ref = f"{provider.provider_id}:{model}"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Delegate to the provider."""
        return await self._provider.embed(self._model, texts)
