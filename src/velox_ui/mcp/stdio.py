"""MCP over stdio: spawn a subprocess, speak JSON-RPC 2.0 over its stdin/stdout.

Messages are newline-delimited JSON objects (the framing the MCP spec's stdio
transport uses — no ``Content-Length`` header as in LSP): the server must not write a
raw newline inside a message, and every line on stdout is exactly one JSON-RPC
message. Anything the child writes to stderr is logged, never parsed, since servers
routinely use it for their own diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from velox_ui.mcp.client import (
    MCP_PROTOCOL_VERSION,
    McpError,
    McpTool,
    McpToolResult,
    next_request_id,
)

__all__ = ["StdioMcpClient"]

_log = logging.getLogger("velox.mcp.stdio")

_STARTUP_TIMEOUT_S = 10.0
_CALL_TIMEOUT_S = 60.0


class StdioMcpClient:
    """An MCP client transport that talks to a child process over stdio.

    Args:
        command: Executable to run.
        args: Arguments.
        env: Extra environment variables merged over the current process's own (a
            decrypted auth token, when the server takes its credential that way,
            arrives here — never written to ``mcp_server.config``).
    """

    __slots__ = (
        "_args",
        "_command",
        "_env",
        "_pending",
        "_process",
        "_read_task",
        "_stderr_task",
    )

    def __init__(
        self, *, command: str, args: list[str] | None = None, env: dict[str, str] | None = None
    ) -> None:
        """Store the launch spec; no process is started yet."""
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    async def initialize(self) -> None:
        """Spawn the subprocess and perform the MCP handshake.

        Raises:
            McpError: If the command cannot be found or started (a wrong path, a
                missing interpreter — e.g. Node not being present in the runtime
                image for an ``npx``-based server — or a permissions error), or if
                the handshake does not complete in time.
        """
        import os

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self._env},
            )
        except OSError as exc:
            raise McpError(
                f"Could not start MCP server command {self._command!r}: {exc}"
            ) from exc
        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        try:
            await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "velox-ui", "version": "1"},
                    },
                ),
                timeout=_STARTUP_TIMEOUT_S,
            )
        except TimeoutError as exc:
            await self.close()
            raise McpError("The MCP server did not respond to initialize in time.") from exc
        await self._notify("notifications/initialized", {})

    async def list_tools(self) -> list[McpTool]:
        """Return the server's tool list."""
        result = await asyncio.wait_for(
            self._request("tools/list", {}), timeout=_CALL_TIMEOUT_S
        )
        return [
            McpTool(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )
            for tool in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Call one tool and return its result."""
        result = await asyncio.wait_for(
            self._request("tools/call", {"name": name, "arguments": arguments}),
            timeout=_CALL_TIMEOUT_S,
        )
        text_parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return McpToolResult(
            content="\n".join(text_parts), is_error=bool(result.get("isError", False))
        )

    async def close(self) -> None:
        """Terminate the subprocess and stop reading."""
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        process = self._process
        self._process = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise McpError("The MCP server process is not running.")
        request_id = next_request_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._process.stdin.drain()
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    _log.warning("non-JSON line from MCP server, ignored")
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            pass

    def _dispatch(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if message_id is None:
            return  # a notification from the server; nothing velox-ui needs yet
        future = self._pending.get(message_id)
        if future is None or future.done():
            return
        if "error" in message:
            error = message["error"]
            future.set_exception(
                McpError(f"MCP server error: {error.get('message', 'unknown error')}")
            )
            return
        future.set_result(message.get("result", {}))

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                _log.debug("mcp server stderr: %s", line.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - best-effort diagnostics only
            _log.debug("mcp server stderr reader stopped: %s", exc)
