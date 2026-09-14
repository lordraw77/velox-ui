"""A deterministic fake embedder, so the RAG test suite never downloads a real ONNX
model or calls a real backend (docs/design/00-overview.md: no test needs a network
call).

The vector is derived from a character-frequency hash of the text, not randomness, so
identical inputs always embed identically and near-identical inputs land near each
other — enough structure for KNN-ordering assertions in tests without pulling in a
real model.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

__all__ = ["FakeEmbedder"]


class FakeEmbedder:
    """An :class:`~velox_ui.rag.embedders.base.Embedder` that hashes text into vectors."""

    def __init__(self, dim: int = 16, ref: str = "fake:test") -> None:
        """Fix the vector width and the ``embedder_ref`` this instance reports."""
        self.dim = dim
        self.ref = ref

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one deterministic unit vector per input text."""
        return [_vector(text, self.dim) for text in texts]


def _vector(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [digest[i % len(digest)] / 255.0 for i in range(dim)]
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]
