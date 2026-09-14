"""The parametrized OpenAI-compatible adapter (ADR-0009).

One streaming loop for every backend that speaks OpenAI's chat-completions protocol:
LM Studio, vLLM, TGI, TabbyAPI, KoboldCpp, LocalAI, Jan, llamafile, mlx_lm, Text
Generation WebUI and anything configured by hand. What differs between them is data —
a :class:`~velox_ui.providers.presets.Preset` — never a branch on the backend's name.

Three behaviours are worth knowing about:

* **Reasoning is read from either field.** Servers that separate a model's thinking put
  it in ``delta.reasoning_content`` (the older, DeepSeek-originated name) or
  ``delta.reasoning`` (the newer one). Both are read, so a thinking model does not look
  silent for the length of its reasoning.
* **Parameters are filtered, then renamed.** Only what the preset lists is sent, so a
  server that rejects unknown fields never sees ``mirostat`` because a user set it for
  a different backend; and ``repeat_penalty`` becomes ``repetition_penalty`` where the
  backend spells it that way.
* **Speed is measured when the backend does not report it.** llama.cpp-derived servers
  send a ``timings`` object, which is used verbatim. Others report only token counts,
  so the generation rate is computed from when the first and last chunks actually
  arrived — observed, never assumed.
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
    ReasoningDelta,
    Status,
    StatusPhase,
    StreamEvent,
    TextDelta,
    Timeouts,
    ToolCallDelta,
    ToolSupport,
    Usage,
)
from velox_ui.providers.errors import (
    AuthError,
    BackendOffline,
    BackendTimeout,
    ContextOverflow,
    ModelLoading,
    ModelNotFound,
    OutOfMemory,
    ProviderError,
    QuotaExceeded,
    RateLimited,
    UnsupportedCapability,
    UpstreamError,
)
from velox_ui.providers.presets import Preset
from velox_ui.providers.tools.translate import to_openai_tools

__all__ = ["OpenAICompatProvider"]

_CONTEXT_WINDOW_KEYS = (
    "context_length",
    "max_model_len",
    "max_context_length",
    "context_window",
)
"""Where OpenAI-compatible servers put a model's window in ``/models``, when they do.

The protocol itself has no such field; vLLM, LM Studio and OpenRouter each added one
under a different name. Reading whichever is present is reading the backend's answer;
inferring a window from the model's name would be guessing.
"""

_CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "too many tokens",
    "exceeds the available context",
    "prompt is too long",
)
_MEMORY_MARKERS = ("out of memory", "cuda error: out of memory", "oom", "insufficient memory")

_SAMPLING_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "typical_p",
    "tfs_z",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "mirostat",
    "mirostat_tau",
    "mirostat_eta",
    "seed",
)

_FINISH: dict[str, Literal["stop", "length", "tool_calls"]] = {
    "stop": "stop",
    "eos": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
}


class OpenAICompatProvider:
    """One configured OpenAI-compatible backend.

    Args:
        provider_id: Identifier used in ``model_ref`` strings and error payloads.
        base_url: Address including the API prefix, e.g. ``http://localhost:1234/v1``.
        client: The shared HTTP client.
        preset: The behaviour of this kind of backend.
        api_key: Optional bearer credential. Held only in memory.
        timeouts: Per-phase timeout budget.
    """

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        preset: Preset,
        api_key: str | None = None,
        timeouts: Timeouts | None = None,
    ) -> None:
        """Bind the adapter to a backend."""
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.preset = preset
        self.is_local = preset.is_local_for(base_url)
        self.supported_params = preset.supported_params()
        self.timeouts = timeouts or Timeouts()
        self._client = client
        # Built once: the headers are the same for every request to this backend.
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._capability_cache: dict[str, Capabilities] = {}

    def __repr__(self) -> str:
        """Describe the adapter without the credential."""
        return (
            f"OpenAICompatProvider(provider_id={self.provider_id!r}, "
            f"preset={self.preset.key!r}, base_url={self.base_url!r})"
        )

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """List models from ``GET /models``."""
        del refresh
        response = await self._request(
            "GET", "/models", timeout_s=self.timeouts.connect_s + 7.0
        )
        payload = response.json()
        entries = payload.get("data", []) if isinstance(payload, dict) else payload
        models: list[ModelInfo] = []
        for entry in entries:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            key = str(entry["id"])
            capabilities = Capabilities(
                context_window=_context_window(entry),
                json_mode=True,
                # The OpenAI protocol has no per-model "supports tools" field, and
                # most current OpenAI-compatible servers (vLLM, TGI, LM Studio, plus
                # every OpenAI-protocol cloud preset) accept the `tools` request field
                # whenever the loaded model's chat template defines one; a model that
                # cannot use it simply never emits a `tool_calls` finish reason, the
                # same graceful ignoring this protocol already has for unsupported
                # sampling parameters. Advertising NATIVE here is what lets
                # ``services/chat.py`` attach tools on the request instead of falling
                # back to the emulated, less reliable prompt-based path.
                tools=ToolSupport.NATIVE,
            )
            self._capability_cache[key] = capabilities
            models.append(
                ModelInfo(
                    key=key,
                    display_name=key,
                    provider_id=self.provider_id,
                    capabilities=capabilities,
                    family=entry.get("owned_by")
                    if isinstance(entry.get("owned_by"), str)
                    else None,
                )
            )
        return models

    async def capabilities(self, model: str, *, refresh: bool = False) -> Capabilities:
        """Return what ``/models`` said about a model.

        The OpenAI protocol has no per-model detail endpoint, so this is the listing's
        answer, cached. A model the listing does not mention gets the protocol's
        baseline rather than an error: the completion request itself is the
        authoritative test of whether it exists.
        """
        if refresh or model not in self._capability_cache:
            try:
                await self.list_models(refresh=refresh)
            except ProviderError:
                return Capabilities()
        return self._capability_cache.get(model, Capabilities())

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream a completion from ``POST /chat/completions``."""
        body = self._encode_request(request)
        timeout = httpx.Timeout(
            connect=self.timeouts.connect_s,
            read=self.timeouts.first_token_s,
            write=self.timeouts.connect_s,
            pool=self.timeouts.connect_s,
        )

        started_generating = False
        finish: Literal["stop", "length", "tool_calls"] = "stop"
        usage: dict[str, Any] | None = None
        timings: dict[str, Any] | None = None
        first_chunk_at: float | None = None
        last_chunk_at: float | None = None
        tool_ids: dict[int, tuple[str, str]] = {}

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                content=msgspec.json.encode(body),
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    await self._raise_for_status(response, model=request.model)

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank separators, `event:` lines, `:` keep-alives
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    chunk = cast(dict[str, Any], msgspec.json.decode(data))

                    if error := chunk.get("error"):
                        raise self._map_error(_error_text(error), None, model=request.model)

                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    if chunk.get("timings"):
                        timings = chunk["timings"]

                    # Only the first choice is streamed: velox-ui never requests n > 1.
                    for choice in (chunk.get("choices") or [])[:1]:
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        text = delta.get("content")
                        calls = delta.get("tool_calls")

                        if (reasoning or text or calls) and not started_generating:
                            started_generating = True
                            yield Status(StatusPhase.GENERATING)

                        if reasoning or text:
                            now = time.perf_counter()
                            if first_chunk_at is None:
                                first_chunk_at = now
                            last_chunk_at = now
                        if reasoning:
                            yield ReasoningDelta(reasoning)
                        if text:
                            yield TextDelta(text)
                        for call in calls or ():
                            yield _tool_delta(call, tool_ids)

                        if reason := choice.get("finish_reason"):
                            finish = _FINISH.get(str(reason), "stop")
        except httpx.ConnectError as exc:
            raise BackendOffline(
                f"Cannot reach {self.preset.label} at {self.base_url}: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"{self.preset.label} at {self.base_url} did not respond in time: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc

        if usage is not None or timings is not None:
            yield _usage(usage, timings, first_chunk_at, last_chunk_at)
        yield Done(finish)

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts via ``POST /embeddings``."""
        response = await self._request(
            "POST",
            "/embeddings",
            json_body={"model": model, "input": list(texts)},
            timeout_s=60.0,
        )
        entries = response.json().get("data")
        if not isinstance(entries, list):
            raise UnsupportedCapability(
                f"{self.preset.label} returned no embeddings for {model!r}.",
                provider_id=self.provider_id,
                model=model,
            )
        ordered = sorted(entries, key=lambda entry: int(entry.get("index", 0)))
        return [cast(list[float], entry["embedding"]) for entry in ordered]

    async def health(self) -> Health:
        """Cheap reachability probe against ``/models``. Never raises.

        A rejected credential is ``degraded`` rather than ``down``: the backend is
        there, and the fix is a key, not starting a server.
        """
        started = time.perf_counter()
        try:
            response = await self._client.get(
                f"{self.base_url}/models",
                headers=self._headers,
                timeout=httpx.Timeout(2.0, connect=2.0),
            )
        except httpx.HTTPError as exc:
            return Health(state=HealthState.DOWN, detail=str(exc)[:200])
        latency_ms = (time.perf_counter() - started) * 1_000.0
        if response.status_code < 400:
            return Health(state=HealthState.UP, latency_ms=latency_ms)
        detail = (
            "the backend rejected the credentials"
            if response.status_code in (401, 403)
            else f"HTTP {response.status_code}"
        )
        return Health(state=HealthState.DEGRADED, latency_ms=latency_ms, detail=detail)

    def _encode_request(self, request: ChatRequest) -> dict[str, Any]:
        """Translate the wire-neutral request into a chat-completions body."""
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            entry: dict[str, Any] = {"role": message.role}
            if isinstance(message.content, str):
                entry["content"] = message.content
            else:
                parts: list[dict[str, Any]] = []
                for part in message.content:
                    if part.kind == "text" and part.text:
                        parts.append({"type": "text", "text": part.text})
                    elif part.kind == "image":
                        url = part.image_url or (
                            f"data:image/png;base64,{part.image_base64}"
                            if part.image_base64
                            else None
                        )
                        if url:
                            parts.append({"type": "image_url", "image_url": {"url": url}})
                entry["content"] = parts
            if message.name:
                entry["name"] = message.name
            if message.tool_call_id:
                entry["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in message.tool_calls
                ]
            messages.append(entry)

        body: dict[str, Any] = {"model": request.model, "messages": messages, "stream": True}
        if self.preset.quirks.stream_usage:
            body["stream_options"] = {"include_usage": True}

        params = request.params
        supported = self.supported_params
        rename = self.preset.rename
        for field in _SAMPLING_FIELDS:
            value = getattr(params, field)
            if value is not None and field in supported:
                body[rename.get(field, field)] = value
        if params.stop and "stop" in supported:
            body[rename.get("stop", "stop")] = list(params.stop)
        if params.max_tokens is not None:
            body[self.preset.quirks.max_tokens_field] = params.max_tokens

        tools = to_openai_tools(request.tools)
        if tools is not None:
            body["tools"] = tools
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": request.json_schema},
            }
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout_s: float = 30.0,
    ) -> httpx.Response:
        """Issue a non-streaming request with typed failures."""
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                headers=self._headers,
                timeout=httpx.Timeout(timeout_s, connect=self.timeouts.connect_s),
            )
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"{self.preset.label} at {self.base_url} did not respond in time: {exc}",
                provider_id=self.provider_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendOffline(
                f"Cannot reach {self.preset.label} at {self.base_url}: {exc}",
                provider_id=self.provider_id,
            ) from exc
        if response.status_code >= 400:
            await self._raise_for_status(response)
        return response

    async def _raise_for_status(
        self, response: httpx.Response, *, model: str | None = None
    ) -> None:
        """Translate an error response into a typed :class:`ProviderError`.

        The body is read explicitly first: on a streaming request httpx has not
        consumed it, and touching ``.json()`` before ``aread()`` raises.
        """
        await response.aread()
        try:
            payload = response.json()
        except ValueError:
            message = response.text or f"HTTP {response.status_code}"
        else:
            message = _error_text(payload.get("error") or payload.get("message") or payload)
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                code = payload["error"].get("code") or payload["error"].get("type")
                if code:
                    message = f"{message} ({code})"
        retry_after = _retry_after(response.headers.get("retry-after"))
        raise self._map_error(
            message, response.status_code, model=model, retry_after=retry_after
        )

    def _map_error(
        self,
        message: str,
        status_code: int | None,
        *,
        model: str | None,
        retry_after: float | None = None,
    ) -> ProviderError:
        """Classify an error by status code first and wording second.

        OpenAI-compatible servers agree on status codes far more than on error bodies,
        so the code decides wherever it is specific enough, and the message only
        separates cases a status code conflates (a 400 for an oversized prompt versus a
        400 for a malformed one).
        """
        lowered = message.lower()
        if status_code in (401, 403):
            return AuthError(
                f"{self.preset.label} rejected the credentials: {message}",
                provider_id=self.provider_id,
                model=model,
            )
        if status_code == 429 or "rate limit" in lowered:
            if "quota" in lowered or "billing" in lowered or "credit" in lowered:
                return QuotaExceeded(message, provider_id=self.provider_id, model=model)
            return RateLimited(
                message, retry_after_s=retry_after, provider_id=self.provider_id, model=model
            )
        if any(marker in lowered for marker in _CONTEXT_MARKERS):
            return ContextOverflow(message, provider_id=self.provider_id, model=model)
        if any(marker in lowered for marker in _MEMORY_MARKERS):
            return OutOfMemory(message, provider_id=self.provider_id, model=model)
        if status_code == 503 and "load" in lowered:
            return ModelLoading(message, provider_id=self.provider_id, model=model)
        if status_code == 404:
            if "model" in lowered:
                return ModelNotFound(message, provider_id=self.provider_id, model=model)
            return UpstreamError(
                f"{self.preset.label} answered 404 at {self.base_url}. "
                f"Check that the base URL includes the API prefix (usually /v1). {message}",
                status_code=404,
                provider_id=self.provider_id,
                model=model,
            )
        return UpstreamError(
            message, status_code=status_code or 502, provider_id=self.provider_id, model=model
        )


def _context_window(entry: dict[str, Any]) -> int | None:
    """Read a model's context window from a ``/models`` entry, if the server gave one."""
    for key in _CONTEXT_WINDOW_KEYS:
        value = entry.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _tool_delta(call: dict[str, Any], known: dict[int, tuple[str, str]]) -> ToolCallDelta:
    """Build a tool-call delta, remembering the id and name the first fragment carried.

    Servers send a call's id and function name only on its first fragment; later
    fragments carry just an index and more argument text.
    """
    index = int(call.get("index", 0))
    function = call.get("function") or {}
    previous_id, previous_name = known.get(index, (f"call_{index}", ""))
    call_id = str(call.get("id") or previous_id)
    name = str(function.get("name") or previous_name)
    known[index] = (call_id, name)
    return ToolCallDelta(
        id=call_id, name=name, arguments_fragment=str(function.get("arguments") or "")
    )


def _usage(
    usage: dict[str, Any] | None,
    timings: dict[str, Any] | None,
    first_chunk_at: float | None,
    last_chunk_at: float | None,
) -> Usage:
    """Build :class:`Usage` from what the backend reported, measuring only what it did not."""
    tokens_in = int((usage or {}).get("prompt_tokens") or (timings or {}).get("prompt_n") or 0)
    tokens_out = int(
        (usage or {}).get("completion_tokens") or (timings or {}).get("predicted_n") or 0
    )
    if timings:
        return Usage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_per_second=timings.get("predicted_per_second"),
            prompt_eval_ms=timings.get("prompt_ms"),
            eval_ms=timings.get("predicted_ms"),
        )

    eval_ms = None
    rate = None
    if (
        first_chunk_at is not None
        and last_chunk_at is not None
        and last_chunk_at > first_chunk_at
    ):
        eval_ms = (last_chunk_at - first_chunk_at) * 1_000.0
        # The first token's arrival starts the clock, so it is not counted in the rate.
        if tokens_out > 1:
            rate = (tokens_out - 1) / (eval_ms / 1_000.0)
    return Usage(
        tokens_in=tokens_in, tokens_out=tokens_out, tokens_per_second=rate, eval_ms=eval_ms
    )


def _error_text(error: object) -> str:
    """Extract a readable message from the many error shapes servers use."""
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        for key in ("message", "detail", "error"):
            value = error.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                return _error_text(value)
    return str(error)


def _retry_after(header: str | None) -> float | None:
    """Parse a ``Retry-After`` header given in seconds. HTTP-date values are ignored."""
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None
