"""Anthropic Messages API adapter (ADR-0018).

Anthropic does not speak the OpenAI wire protocol, so this is a second full adapter
rather than another :mod:`~velox_ui.providers.presets` entry: a different endpoint
(``POST /v1/messages``), a different streaming envelope (named SSE events, not just
``data:`` lines), a required ``max_tokens``, and a system prompt that is a top-level
field rather than a message.

One thing is deliberately not wired: the interface's reasoning on/off control
(:attr:`~velox_ui.providers.base.SamplingParams.think`) does nothing here. Anthropic's
extended thinking is opted into with a ``thinking`` object that also pins
``temperature`` to unset and forces a ``max_tokens`` large enough to hold its
``budget_tokens`` — turning it on from a generic per-model toggle would silently
change what other saved parameters do. What this adapter does do unconditionally is
relay a ``thinking`` content block whenever a model streams one, exactly like Ollama's
``message.thinking``, so a model already in extended-thinking mode is never read as
having gone silent.
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
    ModelNotFound,
    ProviderError,
    QuotaExceeded,
    RateLimited,
    UnsupportedCapability,
    UpstreamError,
)
from velox_ui.providers.tools.translate import anthropic_tool_choice, to_anthropic_tools

__all__ = ["AnthropicProvider"]

_API_VERSION = "2023-06-01"

_DEFAULT_MAX_TOKENS = 4096
"""Anthropic requires ``max_tokens`` on every request. Used when nothing was saved."""

_FINISH: dict[str, Literal["stop", "length", "tool_calls"]] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "refusal": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
}

_ERROR_TYPES: dict[str, type[ProviderError]] = {
    "authentication_error": AuthError,
    "permission_error": AuthError,
    "not_found_error": ModelNotFound,
    "rate_limit_error": RateLimited,
    "overloaded_error": RateLimited,  # the fleet is busy; the fix is the same as a 429
    "billing_error": QuotaExceeded,
}

_CONTEXT_MARKERS = ("context", "too long", "too many tokens", "exceeds")


class AnthropicProvider:
    """One Anthropic account, reached through the Messages API.

    Args:
        provider_id: Identifier used in ``model_ref`` strings and error payloads.
        base_url: Normally ``https://api.anthropic.com/v1``.
        client: The shared HTTP client.
        api_key: The account's API key. Required; Anthropic has no anonymous mode.
        timeouts: Per-phase timeout budget.
    """

    is_local = False
    supported_params = frozenset({"temperature", "top_p", "top_k", "stop", "max_tokens"})

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        api_key: str | None = None,
        timeouts: Timeouts | None = None,
    ) -> None:
        """Bind the adapter to an account."""
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.timeouts = timeouts or Timeouts()
        self._client = client
        self._headers = {
            "anthropic-version": _API_VERSION,
            **({"x-api-key": api_key} if api_key else {}),
        }
        self._capability_cache: dict[str, Capabilities] = {}

    def __repr__(self) -> str:
        """Describe the adapter without the credential."""
        return (
            f"AnthropicProvider(provider_id={self.provider_id!r}, base_url={self.base_url!r})"
        )

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """List models from ``GET /models``."""
        del refresh
        response = await self._request(
            "GET", "/models", timeout_s=self.timeouts.connect_s + 7.0
        )
        models: list[ModelInfo] = []
        for entry in response.json().get("data", []):
            key = str(entry.get("id"))
            capabilities = Capabilities(streaming=True, tools=ToolSupport.NATIVE)
            self._capability_cache[key] = capabilities
            models.append(
                ModelInfo(
                    key=key,
                    display_name=str(entry.get("display_name") or key),
                    provider_id=self.provider_id,
                    capabilities=capabilities,
                    family="anthropic",
                )
            )
        return models

    async def capabilities(self, model: str, *, refresh: bool = False) -> Capabilities:
        """Return what the model listing said about a model.

        Anthropic's API reports no per-model context window or capability detail —
        unlike Ollama's ``/api/show``, there is nothing here to probe — so this is the
        listing's answer, cached, never a guess from the model's name.
        """
        if refresh or model not in self._capability_cache:
            try:
                await self.list_models(refresh=refresh)
            except ProviderError:
                return Capabilities(streaming=True, tools=ToolSupport.NATIVE)
        return self._capability_cache.get(
            model, Capabilities(streaming=True, tools=ToolSupport.NATIVE)
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream a completion from ``POST /messages``."""
        body = self._encode_request(request)
        timeout = httpx.Timeout(
            connect=self.timeouts.connect_s,
            read=self.timeouts.first_token_s,
            write=self.timeouts.connect_s,
            pool=self.timeouts.connect_s,
        )

        started_generating = False
        finish: Literal["stop", "length", "tool_calls"] = "stop"
        tokens_in = 0
        tokens_out = 0
        tool_meta: dict[int, tuple[str, str]] = {}
        first_chunk_at: float | None = None
        last_chunk_at: float | None = None
        event_name = ""

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/messages",
                content=msgspec.json.encode(body),
                headers={**self._headers, "content-type": "application/json"},
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    await self._raise_for_status(response, model=request.model)

                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                        continue
                    if not line.startswith("data:"):
                        continue  # blank separators and `:` keep-alives
                    data = line[len("data:") :].strip()
                    if not data:
                        continue
                    chunk = cast(dict[str, Any], msgspec.json.decode(data))

                    if event_name == "error":
                        raise self._map_error(
                            chunk.get("error") or chunk, None, model=request.model
                        )

                    if event_name == "message_start":
                        tokens_in = int(
                            (chunk.get("message") or {}).get("usage", {}).get("input_tokens")
                            or 0
                        )
                    elif event_name == "content_block_start":
                        index = int(chunk.get("index", 0))
                        block = chunk.get("content_block") or {}
                        kind = str(block.get("type", ""))
                        if kind == "tool_use":
                            tool_meta[index] = (
                                str(block.get("id", "")),
                                str(block.get("name", "")),
                            )
                    elif event_name == "content_block_delta":
                        index = int(chunk.get("index", 0))
                        delta = chunk.get("delta") or {}
                        kind = str(delta.get("type", ""))
                        if kind == "text_delta":
                            if not started_generating:
                                started_generating = True
                                yield Status(StatusPhase.GENERATING)
                            text = str(delta.get("text", ""))
                            now = time.perf_counter()
                            first_chunk_at = first_chunk_at or now
                            last_chunk_at = now
                            yield TextDelta(text)
                        elif kind == "thinking_delta":
                            if not started_generating:
                                started_generating = True
                                yield Status(StatusPhase.GENERATING)
                            yield ReasoningDelta(str(delta.get("thinking", "")))
                        elif kind == "input_json_delta":
                            call_id, name = tool_meta.get(index, (f"toolu_{index}", ""))
                            yield ToolCallDelta(
                                id=call_id,
                                name=name,
                                arguments_fragment=str(delta.get("partial_json", "")),
                            )
                    elif event_name == "message_delta":
                        stop_reason = (chunk.get("delta") or {}).get("stop_reason")
                        if stop_reason:
                            finish = _FINISH.get(str(stop_reason), "stop")
                        tokens_out = int(
                            (chunk.get("usage") or {}).get("output_tokens") or tokens_out
                        )
                    elif event_name == "message_stop":
                        break
        except httpx.ConnectError as exc:
            raise BackendOffline(
                f"Cannot reach Anthropic at {self.base_url}: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"Anthropic did not respond in time: {exc}",
                provider_id=self.provider_id,
                model=request.model,
            ) from exc

        eval_ms = None
        rate = None
        if (
            first_chunk_at is not None
            and last_chunk_at is not None
            and last_chunk_at > first_chunk_at
        ):
            eval_ms = (last_chunk_at - first_chunk_at) * 1_000.0
            if tokens_out > 1:
                rate = (tokens_out - 1) / (eval_ms / 1_000.0)
        yield Usage(
            tokens_in=tokens_in, tokens_out=tokens_out, tokens_per_second=rate, eval_ms=eval_ms
        )
        yield Done(finish)

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Anthropic has no embeddings endpoint."""
        del texts
        raise UnsupportedCapability(
            "Anthropic has no embeddings API; configure a dedicated embedding provider.",
            provider_id=self.provider_id,
            model=model,
        )

    async def health(self) -> Health:
        """Cheap reachability probe against ``/models``. Never raises."""
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
            "the account rejected the credentials"
            if response.status_code in (401, 403)
            else f"HTTP {response.status_code}"
        )
        return Health(state=HealthState.DEGRADED, latency_ms=latency_ms, detail=detail)

    def _encode_request(self, request: ChatRequest) -> dict[str, Any]:
        """Translate the wire-neutral request into a Messages API body.

        The system prompt is pulled out of the message list into the top-level
        ``system`` field, and a stored ``tool`` message becomes a user turn carrying a
        ``tool_result`` block — Anthropic has no ``tool`` role, only a shape for what a
        tool returned.
        """
        system_parts = [
            msg.content
            for msg in request.messages
            if msg.role == "system" and isinstance(msg.content, str) and msg.content
        ]

        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                text = message.content if isinstance(message.content, str) else ""
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or "",
                                "content": text,
                            }
                        ],
                    }
                )
                continue

            blocks: list[dict[str, Any]] = []
            if isinstance(message.content, str):
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
            else:
                for part in message.content:
                    if part.kind == "text" and part.text:
                        blocks.append({"type": "text", "text": part.text})
                    elif part.kind == "image":
                        source = (
                            {"type": "url", "url": part.image_url}
                            if part.image_url
                            else {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": part.image_base64,
                            }
                        )
                        blocks.append({"type": "image", "source": source})
            for call in message.tool_calls or ():
                try:
                    arguments = msgspec.json.decode(call.arguments.encode())
                except msgspec.DecodeError:
                    arguments = {}
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": arguments}
                )
            messages.append(
                {"role": message.role, "content": blocks or [{"type": "text", "text": ""}]}
            )

        params = request.params
        body: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "max_tokens": (
                params.max_tokens
                if params.max_tokens and params.max_tokens > 0
                else _DEFAULT_MAX_TOKENS
            ),
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if params.temperature is not None:
            body["temperature"] = params.temperature
        if params.top_p is not None:
            body["top_p"] = params.top_p
        if params.top_k is not None:
            body["top_k"] = params.top_k
        if params.stop:
            body["stop_sequences"] = list(params.stop)

        if request.tools and (
            request.tool_choice is None or request.tool_choice.mode != "none"
        ):
            tools = to_anthropic_tools(request.tools)
            if tools is not None:
                body["tools"] = tools
            tool_choice = anthropic_tool_choice(request.tool_choice)
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        return body

    async def _request(
        self, method: str, path: str, *, timeout_s: float = 30.0
    ) -> httpx.Response:
        """Issue a non-streaming request with typed failures."""
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers,
                timeout=httpx.Timeout(timeout_s, connect=self.timeouts.connect_s),
            )
        except httpx.TimeoutException as exc:
            raise BackendTimeout(
                f"Anthropic did not respond in time: {exc}", provider_id=self.provider_id
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendOffline(
                f"Cannot reach Anthropic at {self.base_url}: {exc}",
                provider_id=self.provider_id,
            ) from exc
        if response.status_code >= 400:
            await self._raise_for_status(response)
        return response

    async def _raise_for_status(
        self, response: httpx.Response, *, model: str | None = None
    ) -> None:
        """Translate an error response into a typed :class:`ProviderError`."""
        await response.aread()
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text or f"HTTP {response.status_code}"}
        error = payload.get("error") if isinstance(payload, dict) else None
        retry_after = _retry_after(response.headers.get("retry-after"))
        raise self._map_error(
            error or payload, response.status_code, model=model, retry_after=retry_after
        )

    def _map_error(
        self,
        error: object,
        status_code: int | None,
        *,
        model: str | None,
        retry_after: float | None = None,
    ) -> ProviderError:
        """Classify an error by Anthropic's own ``error.type`` first, status second."""
        error_type = str(error.get("type", "")) if isinstance(error, dict) else ""
        message = str(error.get("message", error)) if isinstance(error, dict) else str(error)
        lowered = message.lower()

        error_class = _ERROR_TYPES.get(error_type)
        if error_class is RateLimited:
            return RateLimited(
                message, retry_after_s=retry_after, provider_id=self.provider_id, model=model
            )
        if error_class is not None:
            return error_class(message, provider_id=self.provider_id, model=model)
        if status_code in (401, 403):
            return AuthError(
                f"Anthropic rejected the credentials: {message}",
                provider_id=self.provider_id,
                model=model,
            )
        if status_code == 429:
            return RateLimited(
                message, retry_after_s=retry_after, provider_id=self.provider_id, model=model
            )
        if any(marker in lowered for marker in _CONTEXT_MARKERS):
            return ContextOverflow(message, provider_id=self.provider_id, model=model)
        if status_code == 404 or "model:" in lowered:
            return ModelNotFound(message, provider_id=self.provider_id, model=model)
        return UpstreamError(
            message, status_code=status_code or 502, provider_id=self.provider_id, model=model
        )


def _retry_after(header: str | None) -> float | None:
    """Parse a ``Retry-After`` header given in seconds. HTTP-date values are ignored."""
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None
