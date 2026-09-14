"""The embedder contract.

An embedder turns text into fixed-width vectors. Three implementations exist:
``fastembed_onnx`` (the local default, ADR-0010), ``provider_backed`` (routes through
an existing :class:`~velox_ui.providers.base.Provider`'s ``embed()``), and, in tests,
a deterministic fake (``tests/fakes/embedder.py``) so the suite never downloads a real
model or calls a real backend.

Registration is a plain dict in :mod:`velox_ui.rag.embedders.registry`, not an
entry-point group: ADR-0014 names ``velox_ui.embedders`` as a future plugin group, but
the plugin loader described there does not exist yet in this codebase (checked before
writing this), so building entry-point discovery now would be speculative. The dict is
the same shape a plugin-backed registry would present, so swapping it in later is a
mechanical change confined to this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

__all__ = ["Embedder"]


class Embedder(Protocol):
    """One configured way to turn text into vectors."""

    dim: int
    ref: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts, in order, one vector per input."""
        ...
