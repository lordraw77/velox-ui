"""A fake OpenAI-compatible server.

Replays the chat-completions streaming format as servers actually send it: a first
chunk carrying only the role, one ``data:`` frame per token, a chunk with
``finish_reason``, a usage-only chunk with an empty ``choices`` array when the client
asked for it, and a literal ``data: [DONE]``.

The model name steers behaviour:

* ``reasoner``   — thinking in ``delta.reasoning_content`` before the answer
* ``thinker``    — thinking in ``delta.reasoning``, the newer field name
* ``timed``      — a llama.cpp-derived server: a ``timings`` object, no usage chunk
* ``tools``      — a tool call streamed in fragments, id and name on the first only
* ``slow``       — one token per second
* ``missing``    — 404 with an OpenAI-style ``model_not_found`` body
* ``ctx``        — 400 with vLLM's context-length wording
* ``limited``    — 429 with ``Retry-After: 7``
* ``loading``    — 503 "Model is loading", as TGI answers during startup
* anything else  — a normal generation

Every request body is recorded on ``app.state.requests`` so a test can assert exactly
what the adapter sent, including renamed and filtered parameters.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

TOKENS = ("Local", " models", " are", " first", ".")
REASONING = ("The", " user", " said", " hi", ".")


def _frame(payload: dict[str, Any] | str) -> bytes:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return b"data: " + body.encode("utf-8") + b"\n\n"


def _chunk(model: str, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 1_757_750_400,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def create_app(*, api_key: str | None = None) -> Starlette:
    """Build the fake.

    Args:
        api_key: When set, every request must carry it as a bearer token.
    """
    requests: list[dict[str, Any]] = []

    def _authorised(request: Request) -> bool:
        return api_key is None or request.headers.get("authorization") == f"Bearer {api_key}"

    def _unauthorised() -> JSONResponse:
        return JSONResponse(
            {"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
            status_code=401,
        )

    async def models(request: Request) -> Response:
        if not _authorised(request):
            return _unauthorised()
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": "qwen2.5-7b-instruct",
                        "object": "model",
                        "owned_by": "vllm",
                        "max_model_len": 32768,
                    },
                    {"id": "text-embedding", "object": "model", "owned_by": "vllm"},
                ],
            }
        )

    async def completions(request: Request) -> Response:
        if not _authorised(request):
            return _unauthorised()
        body = await request.json()
        requests.append(body)
        model = str(body.get("model", ""))
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        if model == "missing":
            return JSONResponse(
                {
                    "error": {
                        "message": "The model `missing` does not exist.",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
                status_code=404,
            )
        if model == "ctx":
            return JSONResponse(
                {
                    "object": "error",
                    "type": "BadRequestError",
                    "code": 400,
                    "message": "This model's maximum context length is 4096 tokens. However, "
                    "you requested 5000 tokens.",
                },
                status_code=400,
            )
        if model == "limited":
            return JSONResponse(
                {"error": {"message": "Rate limit reached for requests", "type": "requests"}},
                status_code=429,
                headers={"Retry-After": "7"},
            )
        if model == "loading":
            return JSONResponse({"error": "Model is loading"}, status_code=503)

        async def stream() -> AsyncIterator[bytes]:
            yield _frame(_chunk(model, {"role": "assistant", "content": ""}))
            if model in ("reasoner", "thinker"):
                field = "reasoning_content" if model == "reasoner" else "reasoning"
                for token in REASONING:
                    yield _frame(_chunk(model, {field: token}))
            if model == "tools":
                yield _frame(
                    _chunk(
                        model,
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": ""},
                                }
                            ]
                        },
                    )
                )
                for fragment in ('{"city"', ': "Turin"}'):
                    yield _frame(
                        _chunk(
                            model,
                            {"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]},
                        )
                    )
                yield _frame(_chunk(model, {}, "tool_calls"))
            else:
                for token in TOKENS:
                    if model == "slow":
                        await asyncio.sleep(1.0)
                    else:
                        # Real servers space tokens out; a measured rate needs a
                        # non-zero interval between the first and last chunk.
                        await asyncio.sleep(0.002)
                    yield _frame(_chunk(model, {"content": token}))
                final = _chunk(model, {}, "stop")
                if model == "timed":
                    final["timings"] = {
                        "prompt_n": 12,
                        "prompt_ms": 48.0,
                        "predicted_n": len(TOKENS),
                        "predicted_ms": 100.0,
                        "predicted_per_second": 50.0,
                    }
                yield _frame(final)
            if include_usage and model != "timed":
                yield _frame(
                    {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion.chunk",
                        "model": model,
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": len(TOKENS),
                            "total_tokens": 12 + len(TOKENS),
                        },
                    }
                )
            yield _frame("[DONE]")

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def embeddings(request: Request) -> Response:
        if not _authorised(request):
            return _unauthorised()
        body = await request.json()
        inputs = body.get("input", [])
        # Deliberately out of order: the protocol carries an index, and a client that
        # ignores it pairs vectors with the wrong texts.
        data = [
            {"object": "embedding", "index": i, "embedding": [float(i), 0.5]}
            for i in range(len(inputs))
        ]
        return JSONResponse({"object": "list", "data": list(reversed(data))})

    app = Starlette(
        routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/chat/completions", completions, methods=["POST"]),
            Route("/v1/embeddings", embeddings, methods=["POST"]),
        ]
    )
    app.state.requests = requests
    return app
