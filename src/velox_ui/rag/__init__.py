"""Retrieval-augmented generation: chunking, embedding and vector search.

Imported lazily, at first RAG use or at startup only when a collection already
exists, never from the default cold-start import graph (docs/design/01-repo-layout.md,
"Import-cost rule"). Nothing in this package may be imported from ``app.py`` or
``lifespan.py`` at module scope.
"""

from __future__ import annotations
