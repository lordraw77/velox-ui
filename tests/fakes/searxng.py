"""A fake SearXNG instance, and a small set of "pages" for browse tests.

The query steers behaviour: ``"fail"`` returns a 500, ``"broken"`` returns a body
with no ``results`` key. ``/pages/<name>`` serve fixed HTML for ``web_browse`` tests,
including a redirect chain and one hop that points at a loopback address, to prove
the plugin's SSRF guard checks every hop, not just the first.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

_PAGE_HTML = """
<html><head><style>body{color:red}</style></head>
<body><script>evil()</script><h1>A Page</h1><p>Hello from a fake page.</p></body></html>
"""


def create_app() -> Starlette:
    """Build the fake app."""

    async def search(request: Request) -> Response:
        query = request.query_params.get("q", "")
        if query == "fail":
            return JSONResponse({"error": "boom"}, status_code=500)
        if query == "broken":
            return JSONResponse({"nonsense": True})
        return JSONResponse(
            {
                "results": [
                    {
                        "title": f"Result for {query}",
                        "url": "https://example.invalid/a",
                        "content": "A snippet.",
                    },
                    {
                        "title": "Second result",
                        "url": "https://example.invalid/b",
                        "content": "Another snippet.",
                    },
                ]
            }
        )

    async def page(request: Request) -> Response:
        del request
        return HTMLResponse(_PAGE_HTML)

    async def redirect_to_page(request: Request) -> Response:
        del request
        return RedirectResponse(url="/pages/a")

    async def redirect_to_loopback(request: Request) -> Response:
        del request
        return RedirectResponse(url="http://127.0.0.1:1/private")

    app = Starlette(
        routes=[
            Route("/search", search, methods=["GET"]),
            Route("/pages/a", page, methods=["GET"]),
            Route("/pages/redirect", redirect_to_page, methods=["GET"]),
            Route("/pages/redirect-to-private", redirect_to_loopback, methods=["GET"]),
        ]
    )
    return app
