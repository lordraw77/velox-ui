"""A fake ``llama-server``.

Replays llama.cpp's real wire format: ``data:``-prefixed SSE frames on ``/completion``,
one per token, with a final frame carrying ``stop: true`` and the ``timings`` object
the adapter reads ``predicted_per_second`` and ``prompt_ms`` from.

Prompts steer behaviour so a test can request a condition directly:

* a prompt containing ``__context__`` — the structured context-overflow error
* a prompt containing ``__slow__``    — one token per second
* anything else                        — a normal generation
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

TOKENS = ("The", " quick", " brown", " fox", ".")

PROPS = {
    "n_ctx": 8192,
    "model_path": "/models/qwen2.5-7b-instruct-q4_k_m.gguf",
    "chat_template": "{% for message in messages %}{{ message.content }}{% endfor %}",
    "default_generation_settings": {"n_ctx": 8192},
}


async def _props(request: Request) -> JSONResponse:
    del request
    return JSONResponse(PROPS)


async def _health(request: Request) -> JSONResponse:
    del request
    return JSONResponse({"status": "ok"})


async def _models(request: Request) -> Response:
    del request
    # A single-model server: llama.cpp answers /v1/models, so the adapter prefers it.
    return JSONResponse(
        {"object": "list", "data": [{"id": "qwen2.5-7b-instruct", "object": "model"}]}
    )


def _frame(payload: dict[str, object]) -> bytes:
    """Encode one SSE frame exactly as llama-server does."""
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


async def _completion(request: Request) -> StreamingResponse | JSONResponse:
    body = await request.json()
    prompt = str(body.get("prompt", ""))

    if "__context__" in prompt:
        return JSONResponse(
            {
                "error": {
                    "code": 400,
                    "message": "the request exceeds the available context size",
                    "type": "exceed_context_size_error",
                }
            },
            status_code=400,
        )

    delay = 1.0 if "__slow__" in prompt else 0.0

    async def stream() -> AsyncIterator[bytes]:
        for token in TOKENS:
            if delay:
                await asyncio.sleep(delay)
            yield _frame({"content": token, "stop": False})
        yield _frame(
            {
                "content": "",
                "stop": True,
                "stopped_eos": True,
                "stopped_limit": False,
                "tokens_evaluated": 18,
                "tokens_predicted": len(TOKENS),
                "timings": {
                    "prompt_n": 18,
                    "prompt_ms": 120.5,
                    "predicted_n": len(TOKENS),
                    "predicted_ms": 210.75,
                    "predicted_per_second": 23.7,
                },
            }
        )

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _embedding(request: Request) -> JSONResponse:
    body = await request.json()
    content = body.get("content", [])
    items = content if isinstance(content, list) else [content]
    return JSONResponse([{"embedding": [0.4, 0.5, 0.6]} for _ in items])


def create_app() -> Starlette:
    """Build the fake llama-server ASGI application."""
    return Starlette(
        routes=[
            Route("/props", _props, methods=["GET"]),
            Route("/health", _health, methods=["GET"]),
            Route("/v1/models", _models, methods=["GET"]),
            Route("/completion", _completion, methods=["POST"]),
            Route("/embedding", _embedding, methods=["POST"]),
        ]
    )
