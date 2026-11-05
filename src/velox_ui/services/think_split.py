"""Split inline ``<think>`` reasoning out of a model's text stream.

Many reasoning models served through OpenAI-compatible backends (vLLM, llama.cpp, LM
Studio, ...) do not separate their thinking into a dedicated field. They write it inline:

* ``<think>reasoning</think>answer`` — both tags present, or
* ``reasoning</think>answer`` — the chat template already injected the opening tag into
  the prompt, so only the *closing* tag ever appears in the output.

Without handling, the raw reasoning and a literal ``</think>`` end up in the answer.
:class:`ThinkSplitter` is a small incremental parser that routes the two parts apart. Tags
may be split across chunks, so a possible partial tag is held back until it resolves.

The lone-closing-tag case cannot be known in advance: the text before ``</think>`` has
already been streamed as answer text. The splitter then emits :class:`Retract` with the
text to move into reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass

OPEN = "<think>"
CLOSE = "</think>"
_TAGS = (OPEN, CLOSE)


@dataclass(frozen=True, slots=True)
class Answer:
    """Text that belongs to the visible answer."""

    text: str


@dataclass(frozen=True, slots=True)
class Thought:
    """Text that belongs to the reasoning trace."""

    text: str


@dataclass(frozen=True, slots=True)
class Retract:
    """Answer text already emitted that is in fact reasoning (closing tag seen alone)."""

    text: str


SplitEvent = Answer | Thought | Retract


def _holdback(buffer: str) -> int:
    """Length of the longest buffer suffix that is a proper prefix of a tag."""
    longest = 0
    for tag in _TAGS:
        for size in range(min(len(tag) - 1, len(buffer)), longest, -1):
            if buffer.endswith(tag[:size]):
                longest = size
                break
    return longest


class ThinkSplitter:
    """Incremental splitter. One instance per model round."""

    __slots__ = ("_buffer", "_emitted", "_in_think", "_seen_close", "_seen_open")

    def __init__(self) -> None:
        """Start in answer mode with nothing seen."""
        self._buffer = ""
        self._in_think = False
        self._seen_open = False
        self._seen_close = False
        self._emitted = ""

    def feed(self, chunk: str) -> list[SplitEvent]:
        """Consume a chunk and return the events it resolves."""
        self._buffer += chunk
        return self._drain(final=False)

    def finish(self) -> list[SplitEvent]:
        """Flush anything held back at the end of the stream."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[SplitEvent]:
        events: list[SplitEvent] = []
        while self._buffer:
            tag = CLOSE if self._in_think else OPEN
            other = OPEN if self._in_think else CLOSE
            # Find the first relevant tag. Outside a think block a lone closing tag also
            # counts, provided no think block has been seen yet.
            candidates = [(self._buffer.find(tag), tag)]
            if not self._in_think and not self._seen_open and not self._seen_close:
                candidates.append((self._buffer.find(other), other))
            found = [(i, t) for i, t in candidates if i >= 0]
            if found:
                index, which = min(found)
                self._emit(events, self._buffer[:index])
                self._buffer = self._buffer[index + len(which) :]
                if which == OPEN:
                    self._seen_open = True
                    self._in_think = True
                elif self._in_think:
                    self._seen_close = True
                    self._in_think = False
                else:
                    # Lone closing tag: everything emitted so far was reasoning.
                    self._seen_close = True
                    if self._emitted:
                        events.append(Retract(self._emitted))
                        self._emitted = ""
                continue
            keep = 0 if final else _holdback(self._buffer)
            ready = self._buffer[: len(self._buffer) - keep]
            self._buffer = self._buffer[len(self._buffer) - keep :]
            self._emit(events, ready)
            break
        return events

    def _emit(self, events: list[SplitEvent], text: str) -> None:
        if not text:
            return
        if self._in_think:
            events.append(Thought(text))
        else:
            self._emitted += text
            events.append(Answer(text))
