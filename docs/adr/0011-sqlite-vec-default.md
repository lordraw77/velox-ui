# ADR-0011: sqlite-vec as the default vector store, pgvector optional

**Status:** proposed
**Date:** 2026-09-11

## Context
A separate vector database contradicts the zero-dependency requirement. Corpus sizes for
a self-hosted instance are typically tens of thousands of chunks, not millions.

## Decision
`sqlite-vec` (loadable extension, brute-force KNN over the collection) by default, and
pgvector with an HNSW index when Postgres is configured. Retrieval is hybrid: BM25 from
the FTS index fused with vector results via reciprocal rank fusion, with optional
reranking.

## Consequences
Brute-force KNN is linear in chunk count; it is fast enough to ~100k chunks and the
benchmark records the ceiling. Beyond that, users are pointed at Postgres+pgvector. A
third-party store can be added as a plugin implementing the `VectorStore` protocol.
