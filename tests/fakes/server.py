"""Run a fake backend on a real localhost socket.

The fakes are exercised over a real TCP connection rather than through an in-process
ASGI transport. That matters for two reasons: an in-process transport can buffer a
response that a socket would deliver incrementally, which would quietly invalidate
every test about streaming behaviour; and the benchmark compares a direct call to the
backend against the same call routed through velox-ui, which is only a fair comparison
if both traverse the same kind of connection.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Iterator
from typing import Any

import uvicorn

__all__ = ["FakeServer", "run_fake"]


def _free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class FakeServer:
    """A uvicorn server running an ASGI app in a background thread.

    Attributes:
        base_url: Where the app is reachable.
    """

    def __init__(self, app: Any, *, lifespan: str = "off") -> None:
        """Prepare, but do not start, the server.

        Args:
            app: The ASGI application to serve.
            lifespan: ``"off"`` for the stateless fakes, ``"on"`` for the real
                application, which needs its startup to run.
        """
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                access_log=False,
                lifespan=lifespan,
            )
        )
        self._thread: threading.Thread | None = None

    def start(self, timeout_s: float = 10.0) -> None:
        """Start serving and block until the port accepts connections."""
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        waited = 0.0
        while waited < timeout_s:
            if self._server.started:
                return
            threading.Event().wait(0.02)
            waited += 0.02
        raise RuntimeError("the fake server did not start in time")

    def stop(self) -> None:
        """Ask the server to exit and wait for the thread."""
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10.0)


@contextlib.contextmanager
def run_fake(app: Any, *, lifespan: str = "off") -> Iterator[FakeServer]:
    """Run an ASGI app on a real localhost port for the duration of the block.

    Streaming behaviour cannot be tested through an in-process ASGI transport:
    Starlette's ``TestClient`` runs the application to completion before returning,
    so every response looks buffered regardless of what the application did. Anything
    asserting *when* bytes arrive has to go over a socket.
    """
    server = FakeServer(app, lifespan=lifespan)
    server.start()
    try:
        yield server
    finally:
        server.stop()
