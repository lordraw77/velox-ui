"""Resolves a collection's ``embedder_ref`` to a live :class:`~.base.Embedder`.

A plain function, not an entry-point group: see the module docstring of
``rag/embedders/base.py`` for why entry points are deferred. Adding a third embedder
kind means adding one branch here.

``embedder_ref`` takes one of two forms, both persisted verbatim on the collection:

* ``fastembed:<model-suffix>`` — the local ONNX default, e.g. ``fastembed:bge-small-en-v1.5``.
* ``<provider_id>:<model-key>`` — routed through that provider's ``embed()``, e.g.
  ``ollama:mxbai-embed-large``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from velox_ui.errors import ValidationError

if TYPE_CHECKING:
    from velox_ui.rag.embedders.base import Embedder
    from velox_ui.state import AppState

__all__ = ["dim_for_ref", "resolve_embedder"]

_FASTEMBED_MODELS = {
    "bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
    "bge-base-en-v1.5": "BAAI/bge-base-en-v1.5",
}


def dim_for_ref(embedder_ref: str, *, provider_dim: int | None = None) -> int:
    """Return the vector width for an ``embedder_ref`` without loading anything.

    Args:
        embedder_ref: As stored on the collection.
        provider_dim: Required, and used verbatim, for a provider-backed ref: the
            caller must already know it (probed capability, or user-supplied).

    Raises:
        ValidationError: If the ref names an unknown fastembed model, or a
            provider-backed ref is given with no dimension.
    """
    kind, _, rest = embedder_ref.partition(":")
    if kind == "fastembed":
        from velox_ui.rag.embedders.fastembed_onnx import MODEL_DIMS

        full_name = _FASTEMBED_MODELS.get(rest)
        if full_name is None or full_name not in MODEL_DIMS:
            raise ValidationError(f"Unknown fastembed model: {rest!r}.")
        return MODEL_DIMS[full_name]
    if provider_dim is None:
        raise ValidationError("A provider-backed embedder requires an explicit dimension.")
    return provider_dim


def resolve_embedder(embedder_ref: str, state: AppState, *, dim: int) -> Embedder:
    """Build the embedder a collection's ``embedder_ref`` names.

    Args:
        embedder_ref: As stored on the collection.
        state: Application state, for the provider registry and the data directory.
        dim: The collection's fixed dimension, passed through to a provider-backed
            embedder (fastembed's dimension is implied by the model name).

    Raises:
        ValidationError: If the ref is malformed or names an unknown backend.
    """
    kind, _, rest = embedder_ref.partition(":")
    if not rest:
        raise ValidationError(f"Malformed embedder_ref: {embedder_ref!r}.")

    if kind == "fastembed":
        from velox_ui.rag.embedders.fastembed_onnx import FastEmbedEmbedder

        full_name = _FASTEMBED_MODELS.get(rest)
        if full_name is None:
            raise ValidationError(f"Unknown fastembed model: {rest!r}.")
        return FastEmbedEmbedder(full_name, cache_dir=state.settings.data_dir / "models")

    provider = state.providers.get(kind)
    if provider is None:
        raise ValidationError(f"No such provider: {kind!r}.")
    from velox_ui.rag.embedders.provider_backed import ProviderBackedEmbedder

    return ProviderBackedEmbedder(provider, model=rest, dim=dim)
