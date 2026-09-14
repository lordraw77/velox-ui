"""A fake Anthropic Messages API server.

Replays the real SSE envelope: named ``event:`` lines, a ``message_start`` carrying
input token usage, per-token ``content_block_delta`` frames, a ``message_delta``
carrying the stop reason and output token usage, and ``message_stop``.

The model name steers behaviour:

* ``thinker``  — a ``thinking`` content block streamed before the text, unprompted —
  extended-thinking models do this on their own, not because the adapter asked
* ``tools``    — a ``tool_use`` block streamed as ``input_json_delta`` fragments
* ``missing``  — 404 ``not_found_error``
* ``ctx``      — 400 ``invalid_request_error`` with context-length wording
* ``limited``  — 429 ``rate_limit_error`` with ``Retry-After: 5``
* ``overloaded`` — 529 ``overloaded_error``
* anything else — a normal generation

Every request body is recorded on ``app.state.requests``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

TOKENS = ("Local", " models", " are", " first", ".")
THINKING = ("Let", " me", " think", ".")


def _event(name: str, payload: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


def create_app(*, api_key: str | None = None) -> Starlette:
    """Build the fake.

    Args:
        api_key: When set, every request must carry it as ``x-api-key``.
    """
    requests: list[dict[str, Any]] = []

    def _authorised(request: Request) -> bool:
        return api_key is None or request.headers.get("x-api-key") == api_key

    def _unauthorised() -> JSONResponse:
        return JSONResponse(
            {
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
            status_code=401,
        )

    async def models(request: Request) -> Response:
        if not _authorised(request):
            return _unauthorised()
        return JSONResponse(
            {
                "data": [
                    {
                        "id": "claude-sonnet-5",
                        "type": "model",
                        "display_name": "Claude Sonnet 5",
                    },
                    {
                        "id": "claude-haiku-4-5",
                        "type": "model",
                        "display_name": "Claude Haiku 4.5",
                    },
                ]
            }
        )

    async def messages(request: Request) -> Response:
        if not _authorised(request):
            return _unauthorised()
        body = await request.json()
        requests.append(body)
        model = str(body.get("model", ""))

        if model == "missing":
            return JSONResponse(
                {
                    "type": "error",
                    "error": {"type": "not_found_error", "message": "model: missing"},
                },
                status_code=404,
            )
        if model == "ctx":
            return JSONResponse(
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "prompt is too long: 250000 tokens > 200000 maximum context",
                    },
                },
                status_code=400,
            )
        if model == "limited":
            return JSONResponse(
                {
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "rate limited"},
                },
                status_code=429,
                headers={"Retry-After": "5"},
            )
        if model == "overloaded":
            return JSONResponse(
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "overloaded"},
                },
                status_code=529,
            )

        async def stream() -> AsyncIterator[bytes]:
            yield _event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_fake",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": model,
                        "usage": {"input_tokens": 12, "output_tokens": 0},
                    },
                },
            )

            index = 0
            if model == "thinker":
                yield _event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "thinking", "thinking": ""},
                    },
                )
                for token in THINKING:
                    yield _event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "thinking_delta", "thinking": token},
                        },
                    )
                yield _event(
                    "content_block_stop", {"type": "content_block_stop", "index": index}
                )
                index += 1

            if model == "tools":
                yield _event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_abc",
                            "name": "get_weather",
                            "input": {},
                        },
                    },
                )
                for fragment in ('{"city"', ': "Turin"}'):
                    yield _event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "input_json_delta", "partial_json": fragment},
                        },
                    )
                yield _event(
                    "content_block_stop", {"type": "content_block_stop", "index": index}
                )
                yield _event(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 9},
                    },
                )
            else:
                yield _event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
                for token in TOKENS:
                    yield _event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "text_delta", "text": token},
                        },
                    )
                yield _event(
                    "content_block_stop", {"type": "content_block_stop", "index": index}
                )
                yield _event(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": len(TOKENS)},
                    },
                )
            yield _event("message_stop", {"type": "message_stop"})

        return StreamingResponse(stream(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/messages", messages, methods=["POST"]),
        ]
    )
    app.state.requests = requests
    return app
