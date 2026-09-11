# ADR-0010: FastEmbed/ONNX for embeddings, no torch in the base image

**Status:** proposed
**Date:** 2026-09-11

## Context
The image budget is 250 MB. A torch wheel alone exceeds it several times over, and CUDA
images are gigabytes.

## Decision
Default embeddings come from FastEmbed (ONNX Runtime), with the model downloaded on
first use into the data volume rather than baked into the image. Alternative embedders
(Ollama `/api/embed`, llama.cpp, cloud providers) are selected per collection through the
same `Embedder` protocol. torch, CUDA and sentence-transformers are never dependencies of
the base image; a separate optional image may add them later.

## Consequences
First embedding run pays a model download (~50-100 MB) and the image stays small.
Reranking uses a small ONNX cross-encoder or is disabled.
