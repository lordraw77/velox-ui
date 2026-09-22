"""A scripted Ollama backend, for recording the README demo.

The demo has to be reproducible and has to look like the real thing, which rules out
both a real model (different words, different speed, every run) and a mock-up (not the
real product). So this replays Ollama's own wire format — newline-delimited JSON on
``/api/chat``, one object per token, a final object carrying the nanosecond duration
counters the adapter reads its metrics from — with a fixed answer at a fixed pace.

The pace is the point: ``_TOKEN_DELAY_S`` is chosen so the reply fills roughly five
seconds of an eight-second recording, and the ``eval_*`` counters reported at the end
are computed from the tokens actually sent, so the speed badge in the corner of the
GIF shows a number that matches what the viewer just watched.

Run it directly (``python scripts/demo_backend.py --port 11435``) or let
``scripts/record_demo.py`` start it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

MODEL = "llama3.2:3b"

_TOKEN_DELAY_S = 0.052
"""Seconds between tokens: about 19 tok/s, a plausible 3B model on a CPU-only host."""

ANSWER = """A **stream** in velox-ui is one HTTP response that never buffers.

The provider adapter yields each token as it arrives, the turn service writes \
it to the database and fans it out to every connected reader — no polling, and \
no re-render of the thread.

```python
async for chunk in adapter.chat(messages):
    await turn.append(chunk.text)
    await bus.publish(turn.id, chunk)
```

Because the turn owns the stream rather than the connection, closing the tab \
does not cancel the reply.
"""


def _tokens(text: str) -> list[str]:
    """Split into token-sized pieces, keeping whitespace attached as a model would."""
    pieces: list[str] = []
    for word in text.split(" "):
        pieces.append(word if not pieces else " " + word)
    return pieces


TOKENS = _tokens(ANSWER)


def _line(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8") + b"\n"


async def _tags(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "models": [
                {
                    "name": MODEL,
                    "size": 2_019_393_189,
                    "details": {"family": "llama", "quantization_level": "Q4_K_M"},
                }
            ]
        }
    )


async def _ps(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "models": [
                {
                    "name": MODEL,
                    "size": 2_019_393_189,
                    "size_vram": 2_019_393_189,
                    "expires_at": "2026-09-13T11:01:46.726485964+02:00",
                    "context_length": 4096,
                }
            ]
        }
    )


async def _show(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "details": {
                "format": "gguf",
                "family": "llama",
                "parameter_size": "3.2B",
                "quantization_level": "Q4_K_M",
            },
            "capabilities": ["completion"],
        }
    )


async def _chat(request: Request) -> StreamingResponse:
    del request

    async def stream() -> AsyncIterator[bytes]:
        started = time.perf_counter()
        for token in TOKENS:
            await asyncio.sleep(_TOKEN_DELAY_S)
            yield _line(
                {
                    "model": MODEL,
                    "created_at": "2026-09-12T10:00:00.000000Z",
                    "message": {"role": "assistant", "content": token},
                    "done": False,
                }
            )
        elapsed_ns = int((time.perf_counter() - started) * 1_000_000_000)
        yield _line(
            {
                "model": MODEL,
                "created_at": "2026-09-12T10:00:00.000000Z",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "total_duration": elapsed_ns + 180_000_000,
                "load_duration": 120_000_000,
                "prompt_eval_count": 34,
                "prompt_eval_duration": 60_000_000,
                "eval_count": len(TOKENS),
                "eval_duration": elapsed_ns,
            }
        )

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def create_app() -> Starlette:
    """Build the scripted backend."""
    return Starlette(
        routes=[
            Route("/api/tags", _tags, methods=["GET"]),
            Route("/api/ps", _ps, methods=["GET"]),
            Route("/api/show", _show, methods=["POST"]),
            Route("/api/chat", _chat, methods=["POST"]),
        ]
    )


def main() -> None:
    """Serve the scripted backend on the requested port."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
