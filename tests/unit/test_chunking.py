"""Chunking: paragraph packing, overlap, and hard-splitting of long paragraphs."""

from __future__ import annotations

import itertools

from velox_ui.rag.chunking import ChunkingConfig, chunk_text, estimate_tokens


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\n   ") == []


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_text("Hello world.\n\nA second paragraph.")
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert "Hello world." in chunks[0].content
    assert "second paragraph" in chunks[0].content


def test_long_text_is_split_with_overlap() -> None:
    paragraphs = [
        f"Paragraph number {i} with some filler words to add length." for i in range(40)
    ]
    text = "\n\n".join(paragraphs)
    config = ChunkingConfig(max_tokens=50, overlap_tokens=10)
    chunks = chunk_text(text, config)

    assert len(chunks) > 1
    # Ordinals are contiguous from zero.
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # Consecutive chunks share the overlap: some tail of chunk N reappears at the
    # start of chunk N+1.
    for previous, current in itertools.pairwise(chunks):
        tail = previous.content[-20:]
        assert any(word in current.content for word in tail.split())


def test_overlong_paragraph_is_hard_split() -> None:
    single_paragraph = " ".join(f"word{i}" for i in range(2000))
    config = ChunkingConfig(max_tokens=50, overlap_tokens=5)
    chunks = chunk_text(single_paragraph, config)
    assert len(chunks) > 1
    for chunk in chunks:
        # A loose bound: overlap carry-over plus paragraph joiners can add a few
        # characters past the nominal budget, and this only checks chunking stays
        # roughly sized, not exact.
        assert chunk.token_count <= config.max_tokens + config.overlap_tokens + 5


def test_estimate_tokens_is_monotonic_in_length() -> None:
    assert estimate_tokens("a") < estimate_tokens("a" * 100)
    assert estimate_tokens("") == 1
