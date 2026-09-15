"""A fake OpenAI-compatible media server: images, transcription and speech.

The path steers behaviour so a single fake server can drive success and failure
cases without route-specific fixtures:

* prompt/text ``"broken"`` — a 200 with a body that doesn't match the OpenAI shape,
  to exercise the "unexpected response" error path.
* prompt/text ``"fail"``   — a 500, to exercise the upstream-error path.
* anything else            — a normal, successful response.

Every request is recorded on ``app.state.requests`` so a test can assert what the
plugin actually sent, including the ``Authorization`` header when a key is required.
"""

from __future__ import annotations

import base64
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

_FAKE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")


def create_app(*, api_key: str | None = None) -> Starlette:
    """Build the fake app.

    Args:
        api_key: If set, requests must carry ``Authorization: Bearer <api_key>``.
    """

    def _authorized(request: Request) -> bool:
        if api_key is None:
            return True
        return request.headers.get("authorization") == f"Bearer {api_key}"

    async def generate_images(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        request.app.state.requests.append(body)
        prompt = body.get("prompt", "")
        if prompt == "fail":
            return JSONResponse({"error": "boom"}, status_code=500)
        if prompt == "broken":
            return JSONResponse({"nonsense": True})
        n = body.get("n", 1)
        return JSONResponse({"data": [{"b64_json": _FAKE_PNG_B64} for _ in range(n)]})

    async def transcribe(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        form = await request.form()
        upload = form["file"]
        data = await upload.read()  # type: ignore[union-attr]
        text = data.decode("utf-8", errors="replace")
        request.app.state.requests.append({"model": form.get("model"), "text": text})
        if text == "fail":
            return JSONResponse({"error": "boom"}, status_code=500)
        if text == "broken":
            return JSONResponse({"nonsense": True})
        return JSONResponse({"text": f"transcribed: {text}"})

    async def speech(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        request.app.state.requests.append(body)
        text = body.get("input", "")
        if text == "fail":
            return JSONResponse({"error": "boom"}, status_code=500)
        return Response(content=b"fake-mp3-bytes", media_type="audio/mpeg")

    async def models(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"data": []})

    app = Starlette(
        routes=[
            Route("/v1/images/generations", generate_images, methods=["POST"]),
            Route("/v1/audio/transcriptions", transcribe, methods=["POST"]),
            Route("/v1/audio/speech", speech, methods=["POST"]),
            Route("/v1/models", models, methods=["GET"]),
        ]
    )
    app.state.requests: list[dict[str, Any]] = []
    return app
