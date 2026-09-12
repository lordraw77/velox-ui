"""A fake Ollama server.

It replays Ollama's real wire format rather than an idealised one: newline-delimited
JSON on ``/api/chat``, one object per token, with a final object carrying
``done: true`` and the nanosecond duration counters the adapter reads its metrics
from. If the adapter's parsing is wrong, a contract test against this fails.

Behaviour is steered by the model name so a test can ask for a specific condition
without a configuration handshake:

* ``slow``          — one token per second, the CPU-only host case
* ``oom``           — the out-of-memory error Ollama actually emits
* ``missing``       — the "not found, try pulling it first" error
* ``unloaded``      — absent from ``/api/ps``, so the adapter must announce loading
* ``thinking``      — a reasoning model: text arrives in ``message.thinking`` and
  ``content`` stays empty until the thinking ends, which is what real qwen3 and
  deepseek-r1 responses look like
* anything else     — a normal, fast generation
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

TOKENS = ("Hello", ",", " world", "!", " This", " is", " velox", "-ui", ".")
THINKING_TOKENS = ("Okay", ",", " the", " user", " wants", " a", " greeting", ".")

LOADED_MODELS = ("llama3.2", "slow", "oom", "missing", "thinking")
"""Models `/api/ps` reports as resident. ``unloaded`` is deliberately absent."""


async def _tags(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "models": [
                {
                    "name": "llama3.2",
                    "size": 2_019_393_189,
                    "details": {"family": "llama", "quantization_level": "Q4_K_M"},
                },
                {
                    "name": "nomic-embed-text",
                    "size": 274_302_450,
                    "details": {"family": "nomic-bert", "quantization_level": "F16"},
                },
            ]
        }
    )


async def _show(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(
        {
            "details": {"family": "llama", "quantization_level": "Q4_K_M"},
            "model_info": {"llama.context_length": 131072, "llama.embedding_length": 3072},
            "capabilities": ["completion", "tools"],
            "template": "{{ .System }}\n{{ .Prompt }}",
            "model": body.get("model"),
        }
    )


async def _ps(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "models": [
                {"name": name, "size": 2_019_393_189, "size_vram": 2_019_393_189}
                for name in LOADED_MODELS
            ]
        }
    )


def _line(payload: dict[str, object]) -> bytes:
    """Encode one NDJSON line exactly as Ollama does."""
    return json.dumps(payload).encode("utf-8") + b"\n"


async def _chat(request: Request) -> StreamingResponse | JSONResponse:
    body = await request.json()
    model = str(body.get("model", ""))

    if model == "oom":
        return JSONResponse(
            {
                "error": "model requires more system memory (8.4 GiB) "
                "than is available (3.1 GiB)",
            },
            status_code=500,
        )
    if model == "missing":
        return JSONResponse(
            {"error": 'model "missing" not found, try pulling it first'}, status_code=404
        )

    delay = 1.0 if model == "slow" else 0.0

    async def stream() -> AsyncIterator[bytes]:
        if model == "thinking":
            for token in THINKING_TOKENS:
                yield _line(
                    {
                        "model": model,
                        "created_at": "2026-09-12T10:00:00.000000Z",
                        # Exactly as real Ollama does it: content empty, thinking set.
                        "message": {"role": "assistant", "content": "", "thinking": token},
                        "done": False,
                    }
                )
        for token in TOKENS:
            if delay:
                await asyncio.sleep(delay)
            yield _line(
                {
                    "model": model,
                    "created_at": "2026-09-12T10:00:00.000000Z",
                    "message": {"role": "assistant", "content": token},
                    "done": False,
                }
            )
        yield _line(
            {
                "model": model,
                "created_at": "2026-09-12T10:00:00.000000Z",
                "message": {"role": "assistant", "content": ""},
                "done_reason": "stop",
                "done": True,
                "total_duration": 1_500_000_000,
                "load_duration": 300_000_000,
                "prompt_eval_count": 26,
                "prompt_eval_duration": 200_000_000,
                "eval_count": len(TOKENS),
                "eval_duration": 900_000_000,
            }
        )

    return StreamingResponse(stream(), media_type="application/x-ndjson")


async def _embed(request: Request) -> JSONResponse:
    body = await request.json()
    inputs = body.get("input", [])
    return JSONResponse({"embeddings": [[0.1, 0.2, 0.3] for _ in inputs]})


async def _pull(request: Request) -> StreamingResponse:
    del request

    async def stream() -> AsyncIterator[bytes]:
        yield _line({"status": "pulling manifest"})
        yield _line(
            {
                "status": "pulling 8eeb52dfb3bb",
                "digest": "sha256:8eeb52dfb3bb",
                "total": 100,
                "completed": 50,
            }
        )
        yield _line(
            {
                "status": "pulling 8eeb52dfb3bb",
                "digest": "sha256:8eeb52dfb3bb",
                "total": 100,
                "completed": 100,
            }
        )
        yield _line({"status": "success"})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


async def _delete(request: Request) -> JSONResponse:
    del request
    return JSONResponse({})


async def _copy(request: Request) -> JSONResponse:
    del request
    return JSONResponse({})


def create_app() -> Starlette:
    """Build the fake Ollama ASGI application."""
    return Starlette(
        routes=[
            Route("/api/tags", _tags, methods=["GET"]),
            Route("/api/show", _show, methods=["POST"]),
            Route("/api/ps", _ps, methods=["GET"]),
            Route("/api/chat", _chat, methods=["POST"]),
            Route("/api/embed", _embed, methods=["POST"]),
            Route("/api/pull", _pull, methods=["POST"]),
            Route("/api/delete", _delete, methods=["DELETE"]),
            Route("/api/copy", _copy, methods=["POST"]),
        ]
    )
