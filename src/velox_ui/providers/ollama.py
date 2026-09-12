"""Ollama adapter.

Talks to the native API (``/api/chat``, ``/api/tags``, ``/api/show``, ``/api/ps``,
``/api/pull``, ...) rather than Ollama's OpenAI-compatible shim, because the shim does
not expose model management, load state, or the full ``options`` set this project
needs to control (num_ctx, num_gpu, num_thread, keep_alive, mirostat, ...).

Two things are specific to Ollama and worth calling out:

* **There is no explicit "loading" event on ``/api/chat``.** Ollama simply delays the
  first chunk while it loads weights. This adapter checks ``/api/ps`` before issuing
  the request and emits a ``loading_model`` status itself when the model is not
  already resident, so the UI still gets an honest phase instead of a silent stall
  (ADR-0008).
* **The final NDJSON line carries the real generation metrics** (``prompt_eval_count``,
  ``eval_count``, ``eval_duration``, ...). They are translated into :class:`Usage`
  verbatim; no timing is invented here.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import httpx
import msgspec

from velox_ui.providers.base import (
    Capabilities,
    ChatRequest,
    Done,
    Health,
    HealthState,
    ModelInfo,
    PullProgress,
    ReasoningDelta,
    RunningModel,
    Status,
    StatusPhase,
    StreamEvent,
    TextDelta,
    Timeouts,
    ToolSupport,
    Usage,
)
from velox_ui.providers.errors import (
    BackendOffline,
    BackendTimeout,
    ModelNotFound,
    OutOfMemory,
    ProviderError,
    UnsupportedCapability,
    UpstreamError,
)

__all__ = ["OllamaProvider"]

_NS_PER_MS = 1_000_000.0
_RESIDENCY_TTL_S = 5.0


class OllamaProvider:
    """One configured Ollama host.

    Args:
        provider_id: Identifier used in ``model_ref`` strings and error payloads.
        base_url: e.g. ``http://localhost:11434``.
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
        # Two caches, both on the completion path and both worth roughly a
        # loopback round trip per turn — a meaningful share of the 15 ms
        # time-to-first-token budget (ADR-0004).
        #
        # Capabilities are cached for the life of the process: a model's context
        # window, template and quantisation are fixed once it exists, and a changed
        # model is a different tag. The UI's explicit refresh clears it.
        self._capability_cache: dict[str, Capabilities] = {}
        # Residency is cached only briefly. It genuinely changes — keep_alive expires
        # and the model unloads — but the consequence of a stale answer is cosmetic:
        # a "loading" badge that is a few seconds out of date. Paying a round trip per
        # turn to avoid that is the wrong trade.
        self._residency: tuple[float, frozenset[str]] | None = None

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """List models available on this host, via ``/api/tags``.

        Args:
            refresh: Unused here; Ollama has no separate discovery cache to bypass.
                Kept for interface symmetry with adapters that do cache.
        """
        del refresh
        response = await self._get("/api/tags")
        payload = response.json()
        models: list[ModelInfo] = []
        for entry in payload.get("models", []):
            name = entry["name"]
            details = entry.get("details", {})
            models.append(
                ModelInfo(
                    key=name,
                    display_name=name,
                    provider_id=self.provider_id,
                    family=details.get("family"),
                    capabilities=Capabilities(
                        quantization=details.get("quantization_level"),
                        size_bytes=entry.get("size"),
                    ),
                )
            )
        return models

    async def capabilities(self, model: str, *, refresh: bool = False) -> Capabilities:
        """Probe real capabilities via ``/api/show``, cached per model.

        Context window is read from the model's own parameters (``num_ctx`` in the
        Modelfile, when set) rather than guessed from the model's name — a name like
        ``llama3.2`` says nothing about how this particular copy was configured.

        Args:
            model: The model tag.
            refresh: Bypass the cache, for the UI's explicit refresh action.
        """
        if not refresh and (cached := self._capability_cache.get(model)) is not None:
            return cached
        response = await self._post_json("/api/show", {"model": model})
        payload = response.json()
        details = payload.get("details", {})
        model_info = payload.get("model_info", {})
        capabilities_list = payload.get("capabilities", [])

        context_window = None
        for key, value in model_info.items():
            if key.endswith(".context_length"):
                context_window = int(value)
                break

        capabilities = Capabilities(
            streaming=True,
            tools=ToolSupport.NATIVE if "tools" in capabilities_list else ToolSupport.NONE,
            vision="vision" in capabilities_list,
            json_mode=True,
            reasoning="thinking" in capabilities_list,
            embeddings="embedding" in capabilities_list,
            context_window=context_window,
            quantization=details.get("quantization_level"),
            chat_template=payload.get("template"),
        )
        self._capability_cache[model] = capabilities
        return capabilities

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream a completion from ``/api/chat``."""
        if not await self._is_loaded(request.model):
            yield Status(StatusPhase.LOADING_MODEL)

        body = self._encode_request(request)
        url = f"{self.base_url}/api/chat"
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
                    if not line.strip():
                        continue
                    chunk = cast(dict[str, Any], msgspec.json.decode(line))

                    if error := chunk.get("error"):
                        raise self._map_message(str(error), model=request.model)

                    if not started_generating and not chunk.get("done"):
                        started_generating = True
                        yield Status(StatusPhase.GENERATING)

                    message = chunk.get("message") or {}

                    # Thinking models (qwen3, deepseek-r1, ...) put their reasoning in
                    # a separate `thinking` field and leave `content` empty until it
                    # ends. Dropping it would make such a model look like it produced
                    # nothing at all for several seconds.
                    if thinking := message.get("thinking"):
                        yield ReasoningDelta(thinking)

                    if text := message.get("content"):
                        yield TextDelta(text)

                    if chunk.get("done"):
                        yield self._usage_from(chunk)
                        yield Done("stop" if chunk.get("done_reason") != "length" else "length")
                        return
        except httpx.ConnectError as exc:
            raise BackendOffline(
                f"Cannot reach the Ollama host at {self.base_url}: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"The Ollama host at {self.base_url} did not respond in time: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts via ``/api/embed``."""
        response = await self._post_json("/api/embed", {"model": model, "input": list(texts)})
        payload = response.json()
        embeddings = payload.get("embeddings")
        if embeddings is None:
            raise UnsupportedCapability(
                f"Model {model!r} does not support embeddings.",
                provider_id=self.provider_id,
                model=model,
            )
        return cast(list[list[float]], embeddings)

    async def health(self) -> Health:
        """Cheap reachability probe. Never raises."""
        started = time.perf_counter()
        try:
            await self._client.get(
                f"{self.base_url}/api/tags", timeout=httpx.Timeout(2.0, connect=2.0)
            )
        except httpx.HTTPError as exc:
            return Health(state=HealthState.DOWN, detail=str(exc)[:200])
        return Health(
            state=HealthState.UP, latency_ms=(time.perf_counter() - started) * 1_000.0
        )

    def pull(self, name: str) -> AsyncIterator[PullProgress]:
        """Pull a model, streaming progress (``/api/pull``)."""
        return self._pull(name)

    async def _pull(self, name: str) -> AsyncIterator[PullProgress]:
        url = f"{self.base_url}/api/pull"
        async with self._client.stream("POST", url, json={"model": name}) as response:
            if response.status_code >= 400:
                await self._raise_for_status(response, model=name)
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = cast(dict[str, Any], msgspec.json.decode(line))
                yield PullProgress(
                    status=chunk.get("status", ""),
                    completed_bytes=chunk.get("completed"),
                    total_bytes=chunk.get("total"),
                    digest=chunk.get("digest"),
                )

    async def delete(self, name: str) -> None:
        """Delete a local model (``/api/delete``)."""
        response = await self._client.request(
            "DELETE", f"{self.base_url}/api/delete", json={"model": name}
        )
        if response.status_code >= 400:
            await self._raise_for_status(response, model=name)

    async def copy(self, src: str, dst: str) -> None:
        """Copy a local model under a new name (``/api/copy``)."""
        await self._post_json("/api/copy", {"source": src, "destination": dst})

    async def running(self) -> list[RunningModel]:
        """List models currently loaded in memory (``/api/ps``)."""
        response = await self._get("/api/ps")
        payload = response.json()
        return [
            RunningModel(
                name=entry["name"],
                size_bytes=entry.get("size"),
                vram_bytes=entry.get("size_vram"),
                expires_at_ms=None,
            )
            for entry in payload.get("models", [])
        ]

    async def unload(self, name: str) -> None:
        """Unload a model from memory by requesting ``keep_alive: 0``."""
        self._residency = None  # the UI just changed what is resident
        await self._post_json(
            "/api/chat", {"model": name, "messages": [], "keep_alive": 0}, timeout_s=10.0
        )

    async def _is_loaded(self, model: str) -> bool:
        """Whether ``model`` is currently resident, per ``/api/ps``.

        Best-effort by design: if the host cannot answer, the turn proceeds and the
        real failure surfaces from the completion request itself, with the message
        that actually describes it.
        """
        now = time.monotonic()
        if self._residency is not None and now - self._residency[0] < _RESIDENCY_TTL_S:
            return model in self._residency[1]
        try:
            running = await self.running()
        except ProviderError:
            return False
        self._residency = (now, frozenset(entry.name for entry in running))
        return model in self._residency[1]

    def _encode_request(self, request: ChatRequest) -> dict[str, Any]:
        """Translate the wire-neutral request into Ollama's ``/api/chat`` body."""
        messages = []
        for message in request.messages:
            entry: dict[str, Any] = {"role": message.role}
            if isinstance(message.content, str):
                entry["content"] = message.content
            else:
                text_parts = [part.text for part in message.content if part.kind == "text"]
                images = [
                    part.image_base64
                    for part in message.content
                    if part.kind == "image" and part.image_base64
                ]
                entry["content"] = "\n".join(part for part in text_parts if part)
                if images:
                    entry["images"] = images
            messages.append(entry)

        options = _params_to_options(request)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "options": options,
        }
        if request.params.keep_alive is not None:
            body["keep_alive"] = request.params.keep_alive
        if request.json_schema is not None:
            body["format"] = request.json_schema
        return body

    def _usage_from(self, chunk: dict[str, Any]) -> Usage:
        """Build :class:`Usage` from the final NDJSON line's metrics, verbatim."""
        prompt_tokens = int(chunk.get("prompt_eval_count") or 0)
        eval_tokens = int(chunk.get("eval_count") or 0)
        eval_duration_ns = chunk.get("eval_duration")
        prompt_eval_duration_ns = chunk.get("prompt_eval_duration")

        tokens_per_second = None
        if eval_duration_ns and eval_tokens:
            tokens_per_second = eval_tokens / (float(eval_duration_ns) / 1_000_000_000.0)

        return Usage(
            tokens_in=prompt_tokens,
            tokens_out=eval_tokens,
            tokens_per_second=tokens_per_second,
            prompt_eval_ms=(
                float(prompt_eval_duration_ns) / _NS_PER_MS
                if prompt_eval_duration_ns is not None
                else None
            ),
            eval_ms=float(eval_duration_ns) / _NS_PER_MS
            if eval_duration_ns is not None
            else None,
            cost_micros=0,
        )

    async def _get(self, path: str) -> httpx.Response:
        try:
            response = await self._client.get(
                f"{self.base_url}{path}", timeout=httpx.Timeout(self.timeouts.connect_s + 5.0)
            )
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"The Ollama host at {self.base_url} did not respond in time: {exc}",
                provider_id=self.provider_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendOffline(
                f"Cannot reach the Ollama host at {self.base_url}: {exc}",
                provider_id=self.provider_id,
            ) from exc
        if response.status_code >= 400:
            await self._raise_for_status(response)
        return response

    async def _post_json(
        self, path: str, body: dict[str, Any], *, timeout_s: float = 30.0
    ) -> httpx.Response:
        try:
            response = await self._client.post(
                f"{self.base_url}{path}", json=body, timeout=httpx.Timeout(timeout_s)
            )
        except httpx.ConnectError as exc:
            raise BackendOffline(
                f"Cannot reach the Ollama host at {self.base_url}: {exc}",
                provider_id=self.provider_id,
            ) from exc
        if response.status_code >= 400:
            await self._raise_for_status(response)
        return response

    async def _raise_for_status(
        self, response: httpx.Response, *, model: str | None = None
    ) -> None:
        """Translate an Ollama HTTP error response into a typed :class:`ProviderError`.

        The body is read explicitly first: on a streaming request httpx has not
        consumed it yet, and touching ``.json()`` or ``.text`` before ``aread()``
        raises instead of giving us the error message we came for.
        """
        await response.aread()
        try:
            payload = response.json()
            message = str(payload.get("error", response.text))
        except (ValueError, json.JSONDecodeError):
            message = response.text
        raise self._map_message(message, model=model, status_code=response.status_code)

    def _map_message(
        self, message: str, *, model: str | None = None, status_code: int | None = None
    ) -> ProviderError:
        """Classify an Ollama error message.

        Ollama has no structured error codes, only prose, so this matches the
        substrings the server actually emits.
        """
        lowered = message.lower()
        if "not found" in lowered and "try pulling" in lowered:
            return ModelNotFound(message, provider_id=self.provider_id, model=model)
        if "memory" in lowered or "requires more system" in lowered:
            return OutOfMemory(message, provider_id=self.provider_id, model=model)
        return UpstreamError(
            message,
            provider_id=self.provider_id,
            model=model,
            status_code=status_code or 502,
        )


def _params_to_options(request: ChatRequest) -> dict[str, Any]:
    """Map the wire-neutral sampling params onto Ollama's ``options`` object.

    Unsupported keys are simply absent from the source struct's fields, not silently
    dropped from a superset — msgspec.Struct fields are exactly the sampling
    parameters listed in ``SamplingParams``, so there is nothing to filter here beyond
    ``None`` values, which Ollama's own defaults then apply.
    """
    params = request.params
    options: dict[str, Any] = {}
    mapping: dict[str, Any] = {
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
        "num_predict": params.max_tokens,
        "num_ctx": params.num_ctx,
        "num_gpu": params.num_gpu,
        "num_thread": params.num_thread,
        "num_batch": params.num_batch,
    }
    for key, value in mapping.items():
        if value is not None:
            options[key] = value
    return options
