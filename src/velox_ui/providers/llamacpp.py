"""llama.cpp / ``llama-server`` adapter.

Uses the native endpoints (``/completion``, ``/props``, ``/health``) rather than the
bundled OpenAI-compatible shim, to reach what the shim does not expose: GBNF grammars,
explicit prompt-cache reuse, and the full sampling parameter set.

Two behaviours are specific to this backend:

* **``cache_prompt`` defaults to true.** ``llama-server`` reuses the KV cache of the
  common prefix between requests; without it, every turn of a long conversation would
  re-process the entire context from scratch, which is exactly the kind of latency
  this project exists to avoid.
* **Context size and template come from ``/props``**, read once and cached, rather
  than assumed from the model's file name.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, cast

import httpx
import msgspec

from velox_ui.providers.base import (
    Capabilities,
    ChatRequest,
    Done,
    Health,
    HealthState,
    ModelInfo,
    Status,
    StatusPhase,
    StreamEvent,
    TextDelta,
    Timeouts,
    Usage,
)
from velox_ui.providers.errors import (
    BackendOffline,
    BackendTimeout,
    ContextOverflow,
    ProviderError,
    UnsupportedCapability,
    UpstreamError,
)

__all__ = ["LlamaCppProvider"]


class LlamaCppProvider:
    """One configured ``llama-server`` instance.

    Args:
        provider_id: Identifier used in ``model_ref`` strings and error payloads.
        base_url: e.g. ``http://localhost:8080``.
        client: The shared HTTP client.
        timeouts: Per-phase timeout budget.
    """

    is_local = True

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        timeouts: Timeouts | None = None,
    ) -> None:
        """Bind the adapter to a host."""
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.timeouts = timeouts or Timeouts()
        self._props_cache: dict[str, Any] | None = None

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """List the model this server was started with.

        A single ``llama-server`` process normally serves one model; the model key is
        derived from ``/props`` so the UI has something stable to reference even when
        multi-model routing is not in use. ``/models`` is tried first for servers
        started with several.
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/v1/models",
                timeout=httpx.Timeout(self.timeouts.connect_s + 5.0),
            )
            if response.status_code < 400:
                payload = response.json()
                entries = payload.get("data", [])
                if entries:
                    return [
                        ModelInfo(
                            key=entry["id"],
                            display_name=entry["id"],
                            provider_id=self.provider_id,
                            capabilities=await self.capabilities(entry["id"], refresh=refresh),
                        )
                        for entry in entries
                    ]
        except httpx.HTTPError:
            pass

        props = await self._props(refresh=refresh)
        model_path = str(
            props.get("model_path")
            or props.get("default_generation_settings", {}).get("model", "default")
        )
        key = model_path.rsplit("/", 1)[-1] or "default"
        return [
            ModelInfo(
                key=key,
                display_name=key,
                provider_id=self.provider_id,
                capabilities=await self.capabilities(key, refresh=False),
            )
        ]

    async def capabilities(self, model: str, *, refresh: bool = False) -> Capabilities:
        """Probe capabilities from ``/props``.

        Args:
            model: Unused beyond interface compatibility: a single ``llama-server``
                process serves one loaded model regardless of the key requested.
            refresh: Bypass the cached ``/props`` response.
        """
        del model
        props = await self._props(refresh=refresh)
        context_window = props.get("n_ctx") or props.get("default_generation_settings", {}).get(
            "n_ctx"
        )
        return Capabilities(
            streaming=True,
            grammar=True,
            json_mode=True,
            context_window=int(context_window) if context_window else None,
            chat_template=props.get("chat_template"),
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream a completion from ``/completion``."""
        body = self._encode_request(request)
        url = f"{self.base_url}/completion"
        timeout = httpx.Timeout(
            connect=self.timeouts.connect_s,
            read=self.timeouts.first_token_s,
            write=self.timeouts.connect_s,
            pool=self.timeouts.connect_s,
        )

        started_generating = False
        try:
            async with self._client.stream("POST", url, json=body, timeout=timeout) as response:
                if response.status_code >= 400:
                    await self._raise_for_status(response, model=request.model)

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload:
                        continue
                    chunk = cast(dict[str, Any], msgspec.json.decode(payload))

                    if "error" in chunk:
                        raise self._map_error(chunk["error"], model=request.model)

                    if not started_generating:
                        started_generating = True
                        yield Status(StatusPhase.GENERATING)

                    content = chunk.get("content", "")
                    if content:
                        yield TextDelta(content)

                    if chunk.get("stop"):
                        yield self._usage_from(chunk)
                        reason: Literal["stop", "length"] = (
                            "length" if chunk.get("stopped_limit") else "stop"
                        )
                        yield Done(reason)
                        return
        except httpx.ConnectError as exc:
            raise BackendOffline(
                f"Cannot reach the llama.cpp server at {self.base_url}: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"The llama.cpp server at {self.base_url} did not respond in time: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts via ``/embedding``. Requires the server to run with ``--embedding``."""
        response = await self._client.post(
            f"{self.base_url}/embedding",
            json={"content": list(texts)},
            timeout=httpx.Timeout(30.0),
        )
        if response.status_code >= 400:
            if response.status_code == 501:
                raise UnsupportedCapability(
                    "This llama-server instance was not started with --embedding.",
                    provider_id=self.provider_id,
                    model=model,
                )
            await self._raise_for_status(response, model=model)
        payload = response.json()
        if isinstance(payload, list):
            return [entry["embedding"] for entry in payload]
        return [payload["embedding"]]

    async def health(self) -> Health:
        """Cheap reachability probe via ``/health``. Never raises.

        ``llama-server`` answers ``503`` with ``{"status": "loading model"}`` while a
        model is loading, which this adapter reports as ``degraded`` rather than
        ``down`` — the process is alive, just not ready yet.
        """
        started = time.perf_counter()
        try:
            response = await self._client.get(
                f"{self.base_url}/health", timeout=httpx.Timeout(2.0, connect=2.0)
            )
        except httpx.HTTPError as exc:
            return Health(state=HealthState.DOWN, detail=str(exc)[:200])
        latency_ms = (time.perf_counter() - started) * 1_000.0
        if response.status_code == 200:
            return Health(state=HealthState.UP, latency_ms=latency_ms)
        try:
            detail = str(response.json().get("status", response.text))
        except (ValueError, KeyError):
            detail = response.text
        return Health(state=HealthState.DEGRADED, latency_ms=latency_ms, detail=detail[:200])

    def _encode_request(self, request: ChatRequest) -> dict[str, Any]:
        """Translate the wire-neutral request into llama.cpp's ``/completion`` body.

        ``/completion`` takes a raw prompt, not a message list, so messages are joined
        with the server's own chat template applied by the ``/v1/chat/completions``
        shim would be more convenient — but that shim is exactly what does not expose
        grammars and prompt-cache control. Instead this builds a plain transcript; a
        production adapter would render the model's own template (read from
        ``/props``) rather than this placeholder role-prefixed format.
        """
        prompt_lines = []
        for message in request.messages:
            text = (
                message.content
                if isinstance(message.content, str)
                else "\n".join(part.text or "" for part in message.content)
            )
            prompt_lines.append(f"<|{message.role}|>\n{text}")
        prompt_lines.append("<|assistant|>\n")
        prompt = "\n".join(prompt_lines)

        params = request.params
        body: dict[str, Any] = {
            "prompt": prompt,
            "stream": True,
            "cache_prompt": params.cache_prompt if params.cache_prompt is not None else True,
        }
        mapping = {
            "temperature": params.temperature,
            "top_p": params.top_p,
            "top_k": params.top_k,
            "min_p": params.min_p,
            "typical_p": params.typical_p,
            "tfs_z": params.tfs_z,
            "repeat_penalty": params.repeat_penalty,
            "presence_penalty": params.presence_penalty,
            "frequency_penalty": params.frequency_penalty,
            "mirostat": params.mirostat,
            "mirostat_tau": params.mirostat_tau,
            "mirostat_eta": params.mirostat_eta,
            "penalize_nl": params.penalize_nl,
            "seed": params.seed,
            "stop": list(params.stop) if params.stop else None,
            "n_predict": params.max_tokens,
        }
        for key, value in mapping.items():
            if value is not None:
                body[key] = value
        if request.grammar:
            body["grammar"] = request.grammar
        elif request.json_schema is not None:
            body["json_schema"] = request.json_schema
        return body

    def _usage_from(self, chunk: dict[str, Any]) -> Usage:
        """Build :class:`Usage` from the final SSE frame's ``timings``, verbatim."""
        timings = chunk.get("timings") or {}
        tokens_in = int(chunk.get("tokens_evaluated") or 0)
        tokens_out = int(chunk.get("tokens_predicted") or 0)
        return Usage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_per_second=timings.get("predicted_per_second"),
            prompt_eval_ms=timings.get("prompt_ms"),
            eval_ms=timings.get("predicted_ms"),
            cost_micros=0,
        )

    async def _props(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return ``/props``, cached after the first successful call."""
        if self._props_cache is not None and not refresh:
            return self._props_cache
        response = await self._client.get(
            f"{self.base_url}/props", timeout=httpx.Timeout(self.timeouts.connect_s + 5.0)
        )
        if response.status_code >= 400:
            await self._raise_for_status(response)
        self._props_cache = response.json()
        return self._props_cache

    async def _raise_for_status(
        self, response: httpx.Response, *, model: str | None = None
    ) -> None:
        """Translate an HTTP error response into a typed :class:`ProviderError`.

        The body is read explicitly first: on a streaming request httpx has not
        consumed it yet, and touching ``.json()`` before ``aread()`` raises instead of
        giving us the error payload we came for.
        """
        await response.aread()
        try:
            payload = response.json()
            error = payload.get("error", payload)
        except ValueError:
            raise UpstreamError(
                response.text or f"HTTP {response.status_code}",
                provider_id=self.provider_id,
                model=model,
                status_code=response.status_code,
            ) from None
        raise self._map_error(error, model=model, status_code=response.status_code)

    def _map_error(
        self, error: object, *, model: str | None = None, status_code: int | None = None
    ) -> ProviderError:
        """Classify a llama.cpp error payload.

        ``llama-server`` reports context overflow with ``type: "exceed_context_size_error"``
        when structured, which is matched before falling back to substring heuristics
        on whatever message it did send.
        """
        if isinstance(error, dict):
            error_type = str(error.get("type", ""))
            message = str(error.get("message", error))
            if "context" in error_type or "context" in message.lower():
                return ContextOverflow(message, provider_id=self.provider_id, model=model)
        else:
            message = str(error)
        return UpstreamError(
            message, provider_id=self.provider_id, model=model, status_code=status_code or 502
        )
