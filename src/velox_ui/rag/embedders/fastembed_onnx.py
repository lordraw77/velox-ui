"""The default local embedder: FastEmbed over ONNX Runtime (ADR-0010).

``fastembed`` is an optional dependency (the ``rag`` extra) — not in the base image —
because it pulls in ``onnxruntime`` and, on first use, downloads the ONNX model itself
into the data directory. Both imports happen only inside :meth:`FastEmbedEmbedder.embed`
and its constructor, never at module load, so a default install that never touches RAG
never pays either cost (docs/design/01-repo-layout.md, import-cost rule).

The default model is ``BAAI/bge-small-en-v1.5`` (384 dimensions, ~67 MB quantized
ONNX), matching ``embedder_ref = "fastembed:bge-small-en-v1.5"`` from
docs/design/02-db-schema.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from velox_ui.errors import ValidationError

__all__ = ["DEFAULT_MODEL", "MODEL_DIMS", "FastEmbedEmbedder"]

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Dimensions for the handful of models this UI offers in its picker; fastembed itself
# knows every supported model's dimension, but hardcoding the ones offered here means
# a collection's `dim` is known before the (possibly not-yet-downloaded) model loads.
MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
}


class FastEmbedEmbedder:
    """Wraps ``fastembed.TextEmbedding``. One instance per model, cached by the caller."""

    def __init__(
        self, model_name: str = DEFAULT_MODEL, *, cache_dir: Path | None = None
    ) -> None:
        """Store configuration; nothing is imported or downloaded yet.

        Raises:
            ValidationError: If ``model_name`` is not one this instance offers.
        """
        if model_name not in MODEL_DIMS:
            raise ValidationError(f"Unsupported fastembed model: {model_name!r}.")
        self.ref = f"fastembed:{model_name.split('/')[-1].lower()}"
        self.dim = MODEL_DIMS[model_name]
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            kwargs: dict[str, Any] = {}
            if self._cache_dir is not None:
                kwargs["cache_dir"] = str(self._cache_dir)
            self._model = TextEmbedding(model_name=self._model_name, **kwargs)
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts, running the ONNX session in a worker thread.

        FastEmbed's API is synchronous and can block on a first-run model download;
        ``asyncio.to_thread`` keeps that off the event loop.
        """
        return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        return [vector.tolist() for vector in model.embed(texts)]
