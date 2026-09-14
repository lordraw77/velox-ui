"""Splits document text into overlapping chunks for embedding and retrieval.

One strategy, deliberately: paragraph-aware packing with a fixed token budget and a
fixed overlap (ADR-0019). A document is split on blank lines into paragraphs, then
paragraphs are packed into chunks up to ``max_tokens``; a paragraph longer than the
budget on its own is hard-split on whitespace. Each new chunk after the first repeats
the trailing ``overlap_tokens`` of the previous one, so a sentence split across a chunk
boundary is not made unretrievable by exactly one query.

Token counting has no tokenizer dependency: it approximates one token as four
characters, which is close enough for chunk sizing and keeps the base image free of a
BPE vocabulary. It is never used for anything that must be exact (billing, context
windows) — only for keeping chunks a sane, roughly consistent size.
"""

from __future__ import annotations

import msgspec

__all__ = ["Chunk", "ChunkingConfig", "chunk_text", "estimate_tokens"]

_CHARS_PER_TOKEN = 4


class ChunkingConfig(msgspec.Struct, frozen=True):
    """Chunk sizing, stored per collection (``collection.chunking``)."""

    max_tokens: int = 400
    overlap_tokens: int = 60


class Chunk(msgspec.Struct, frozen=True):
    """One packed chunk of source text, before it is written to the ``chunk`` table."""

    ordinal: int
    content: str
    token_count: int


def estimate_tokens(text: str) -> int:
    """Approximate a token count from character length. See module docstring."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n")]
    return [p for p in paragraphs if p]


def _hard_split(paragraph: str, max_chars: int) -> list[str]:
    """Split an over-long paragraph on whitespace into pieces under ``max_chars``."""
    words = paragraph.split()
    pieces: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and length + added > max_chars:
            pieces.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += added
    if current:
        pieces.append(" ".join(current))
    return pieces or [paragraph]


def chunk_text(text: str, config: ChunkingConfig | None = None) -> list[Chunk]:
    """Pack ``text`` into overlapping chunks per ``config``.

    Args:
        text: Plain text (already extracted from markdown, PDF, etc.).
        config: Sizing and overlap. Defaults to :class:`ChunkingConfig`'s own defaults.

    Returns:
        Chunks in document order, ``ordinal`` starting at 0. Empty input yields an
        empty list.
    """
    config = config or ChunkingConfig()
    max_chars = config.max_tokens * _CHARS_PER_TOKEN
    overlap_chars = min(config.overlap_tokens * _CHARS_PER_TOKEN, max_chars // 2)

    pieces: list[str] = []
    for paragraph in _split_paragraphs(text):
        if len(paragraph) > max_chars:
            pieces.extend(_hard_split(paragraph, max_chars))
        else:
            pieces.append(paragraph)

    chunks: list[Chunk] = []
    buffer = ""
    for piece in pieces:
        candidate = f"{buffer}\n\n{piece}" if buffer else piece
        if buffer and len(candidate) > max_chars:
            chunks.append(
                Chunk(ordinal=len(chunks), content=buffer, token_count=estimate_tokens(buffer))
            )
            carry = buffer[-overlap_chars:] if overlap_chars else ""
            buffer = f"{carry}\n\n{piece}" if carry else piece
        else:
            buffer = candidate

    if buffer:
        chunks.append(
            Chunk(ordinal=len(chunks), content=buffer, token_count=estimate_tokens(buffer))
        )
    return chunks
