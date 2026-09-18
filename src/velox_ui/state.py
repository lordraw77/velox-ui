"""Application state.

One object holds everything created at startup and shared by every request: the
settings, the database, the secret box, and the set of background tasks. It is attached
to ``app.state`` so nothing has to reach for a module-level global, which is what makes
it possible to run several independent instances inside one test process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from velox_ui.db.engine import Database
from velox_ui.providers.registry import ProviderRegistry
from velox_ui.security.crypto import SecretBox
from velox_ui.settings import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only; importing httpx here would
    # undo the whole point of the lazy client below.
    import httpx

    from velox_ui.mcp.manager import McpManager
    from velox_ui.plugins.loader import PluginRegistry
    from velox_ui.services.model_jobs import ModelJobs
    from velox_ui.services.model_params import ModelParamsStore
    from velox_ui.services.rag_jobs import RagJobs
    from velox_ui.services.turns import TurnBroker

__all__ = ["AppState"]

_log = logging.getLogger("velox.state")


class AppState:
    """Long-lived objects shared across requests.

    Attributes:
        settings: The validated configuration.
        db: The database.
        secrets: The credential encryption box.
        http: The one HTTP client every provider adapter shares.
        providers: The configured inference backends.
        local_user_id: The built-in account used when authentication is disabled.
        started_at_ms: When the process finished starting, for uptime reporting.
    """

    __slots__ = (
        "_http",
        "_mcp",
        "_model_jobs",
        "_model_params",
        "_plugins",
        "_rag_jobs",
        "_tasks",
        "_turns",
        "db",
        "discovery",
        "local_user_id",
        "providers",
        "secrets",
        "settings",
        "started_at_ms",
    )

    def __init__(self, settings: Settings, db: Database) -> None:
        """Build the state around an already-created database."""
        self.settings = settings
        self.db = db
        self.secrets = SecretBox(settings.secret_key)
        self.providers = ProviderRegistry(lambda: self.http)
        self.local_user_id: str | None = None
        self.started_at_ms = 0
        self._http: httpx.AsyncClient | None = None
        self.discovery: asyncio.Task[Any] | None = None
        self._model_jobs: ModelJobs | None = None
        self._model_params: ModelParamsStore | None = None
        self._rag_jobs: RagJobs | None = None
        self._mcp: McpManager | None = None
        self._plugins: PluginRegistry | None = None
        self._turns: TurnBroker | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def model_jobs(self) -> ModelJobs:
        """Model downloads and creations in progress, created on first use."""
        if self._model_jobs is None:
            from velox_ui.services.model_jobs import ModelJobs

            self._model_jobs = ModelJobs(self)
        return self._model_jobs

    @property
    def model_params(self) -> ModelParamsStore:
        """Saved per-model sampling parameters, created on first use."""
        if self._model_params is None:
            from velox_ui.services.model_params import ModelParamsStore

            self._model_params = ModelParamsStore(self)
        return self._model_params

    @property
    def rag_jobs(self) -> RagJobs:
        """RAG ingest/embed jobs in progress, created on first use."""
        if self._rag_jobs is None:
            from velox_ui.services.rag_jobs import RagJobs

            self._rag_jobs = RagJobs(self)
        return self._rag_jobs

    @property
    def mcp(self) -> McpManager:
        """MCP server connections, tool cache and the approval gate, created on first use."""
        if self._mcp is None:
            from velox_ui.mcp.manager import McpManager

            self._mcp = McpManager(self)
        return self._mcp

    @property
    def turns(self) -> TurnBroker:
        """Turns running independently of the connections reading them, on first use."""
        if self._turns is None:
            from velox_ui.services.turns import TurnBroker

            self._turns = TurnBroker(self.spawn)
        return self._turns

    @property
    def plugins(self) -> PluginRegistry:
        """Configured image/voice plugins (ADR-0014), created on first use."""
        if self._plugins is None:
            from velox_ui.plugins.loader import PluginRegistry

            self._plugins = PluginRegistry(self)
        return self._plugins

    @property
    def http(self) -> httpx.AsyncClient:
        """The shared HTTP client, created the first time something needs it.

        Importing httpx and constructing an HTTP/2-capable client costs well over a
        tenth of the one-second cold-start budget, and an instance that has not been
        asked to talk to a backend yet has no use for either. The cost moves to the
        first provider call, where it is small next to the model's own latency.
        """
        if self._http is None:
            from velox_ui.providers.httpclient import build_http_client

            self._http = build_http_client()
        return self._http

    async def http_client(self) -> httpx.AsyncClient:
        """The shared HTTP client, built in a worker thread if it does not exist yet.

        Background work that starts together with the server uses this instead of
        :attr:`http`. Importing httpx and building an HTTP/2 client is roughly 70 ms of
        synchronous work, and done on the event loop it would run before the server
        answers its first request — measured as exactly that much added to every
        zero-configuration cold start. In a thread it interleaves with serving instead.

        If a request built the client on the loop in the meantime, that one wins and
        the thread's copy is closed, so there is still exactly one client.
        """
        if self._http is not None:
            return self._http
        return await self._adopt(await asyncio.to_thread(_build_http_client))

    async def _adopt(self, client: httpx.AsyncClient) -> httpx.AsyncClient:
        """Keep a freshly built client unless another one appeared while it was built."""
        if self._http is None:
            self._http = client
            return client
        await client.aclose()
        return self._http

    async def discovery_settled(self, timeout_s: float = 3.0) -> None:
        """Wait, briefly, for first-run autodiscovery to finish if it is still running.

        Discovery runs in the background so it never delays readiness; this keeps the
        first model listing after a start from coming back empty just because the probe
        had not finished yet. Never raises and never cancels the discovery.
        """
        task = self.discovery
        if task is None or task.done():
            return
        await asyncio.wait({task}, timeout=timeout_s)

    async def close_http(self) -> None:
        """Close the HTTP client if one was ever built."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine in the background without awaiting it.

        A strong reference is kept until completion, because the event loop only holds
        a weak one and an unreferenced task can be garbage collected mid-flight.
        Failures are logged rather than swallowed: a background write that silently
        disappears is the kind of bug that surfaces days later as missing data.
        """
        self.spawn(coro)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Start a tracked background task and return it.

        Use this over :meth:`schedule` when the work must survive the cancellation of
        whatever started it. A request task that is cancelled — a client closing a
        stream — carries its cancellation into every ``await`` still running inside it,
        including the ``rollback`` a database session performs on the way out. That
        leaks the connection. Work that must complete regardless belongs in its own
        task, which this creates.
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finish_task)
        return task

    def _finish_task(self, task: asyncio.Task[Any]) -> None:
        """Drop the reference and report an unexpected failure."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _log.error("background task failed", exc_info=error)

    async def drain(self, grace_s: float = 5.0) -> None:
        """Wait for outstanding background tasks during shutdown."""
        if not self._tasks:
            return
        pending = list(self._tasks)
        done, still_running = await asyncio.wait(pending, timeout=grace_s)
        del done
        for task in still_running:
            task.cancel()


def _build_http_client() -> httpx.AsyncClient:
    """Import httpx and build the client; run in a worker thread by ``http_client``."""
    from velox_ui.providers.httpclient import build_http_client

    return build_http_client()
