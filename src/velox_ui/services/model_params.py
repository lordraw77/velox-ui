"""A user's saved sampling parameters per model.

These are applied on every turn, which puts the lookup on the completion path. So they
are cached in-process — including the answer "this user has saved nothing for this
model", which is by far the common case and would otherwise cost a query per turn for
nothing. The cache is bounded and written through: a save or reset replaces the entry
in the same request, so the next turn sees it.

Per-turn overrides sent with a completion request win over saved values, field by
field.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Annotated, Any

import msgspec

from velox_ui.db.repositories.providers import ModelParamsRepository
from velox_ui.providers.base import SamplingParams
from velox_ui.state import AppState

__all__ = ["ModelParamsInput", "ModelParamsStore", "merge_params"]

_CACHE_ENTRIES = 2_048

_Probability = Annotated[float, msgspec.Meta(ge=0.0, le=1.0)]


class ModelParamsInput(msgspec.Struct, forbid_unknown_fields=True, omit_defaults=True):
    """Validated parameters as submitted by the interface.

    The ranges are wide on purpose — they reject nonsense such as a negative
    temperature, not unusual-but-valid choices — and the backend applies its own
    limits beyond them.
    """

    temperature: Annotated[float, msgspec.Meta(ge=0.0, le=5.0)] | None = None
    top_p: _Probability | None = None
    top_k: Annotated[int, msgspec.Meta(ge=0, le=100_000)] | None = None
    min_p: _Probability | None = None
    typical_p: _Probability | None = None
    tfs_z: Annotated[float, msgspec.Meta(ge=0.0, le=100.0)] | None = None
    repeat_penalty: Annotated[float, msgspec.Meta(ge=0.0, le=10.0)] | None = None
    presence_penalty: Annotated[float, msgspec.Meta(ge=-2.0, le=2.0)] | None = None
    frequency_penalty: Annotated[float, msgspec.Meta(ge=-2.0, le=2.0)] | None = None
    mirostat: Annotated[int, msgspec.Meta(ge=0, le=2)] | None = None
    mirostat_tau: Annotated[float, msgspec.Meta(ge=0.0, le=100.0)] | None = None
    mirostat_eta: Annotated[float, msgspec.Meta(ge=0.0, le=10.0)] | None = None
    penalize_nl: bool | None = None
    seed: int | None = None
    stop: (
        Annotated[
            tuple[Annotated[str, msgspec.Meta(min_length=1, max_length=200)], ...],
            msgspec.Meta(max_length=16),
        ]
        | None
    ) = None
    # Ollama uses -1 for "no limit" and -2 for "fill the context".
    max_tokens: Annotated[int, msgspec.Meta(ge=-2, le=10_000_000)] | None = None
    num_ctx: Annotated[int, msgspec.Meta(ge=64, le=100_000_000)] | None = None
    num_gpu: Annotated[int, msgspec.Meta(ge=-1, le=100_000)] | None = None
    num_thread: Annotated[int, msgspec.Meta(ge=1, le=4_096)] | None = None
    num_batch: Annotated[int, msgspec.Meta(ge=1, le=1_000_000)] | None = None
    keep_alive: Annotated[str, msgspec.Meta(pattern=r"^-?\d+(\.\d+)?(ms|s|m|h)?$")] | None = (
        None
    )
    cache_prompt: bool | None = None
    think: bool | None = None

    def to_params(self) -> SamplingParams:
        """Convert to the wire-neutral struct adapters consume."""
        return msgspec.convert(msgspec.to_builtins(self), SamplingParams)


def merge_params(saved: SamplingParams | None, turn: SamplingParams) -> SamplingParams:
    """Overlay per-turn values on saved ones, field by field.

    Returns ``turn`` itself when nothing is saved, so the common case allocates
    nothing.
    """
    if saved is None:
        return turn
    changes = {
        field: value
        for field in turn.__struct_fields__
        if (value := getattr(turn, field)) is not None
    }
    return msgspec.structs.replace(saved, **changes) if changes else saved


def _compact(params: SamplingParams) -> dict[str, Any]:
    """The set fields only, as stored."""
    return {
        field: value
        for field in params.__struct_fields__
        if (value := getattr(params, field)) is not None
    }


class ModelParamsStore:
    """Cached access to saved parameters.

    Args:
        state: Application state, for the database.
    """

    def __init__(self, state: AppState) -> None:
        """Start with an empty cache."""
        self._state = state
        self._cache: OrderedDict[tuple[str, str], SamplingParams | None] = OrderedDict()

    async def get(self, user_id: str, model_ref: str) -> SamplingParams | None:
        """The saved parameters, or ``None``. Cached, including the absence."""
        key = (user_id, model_ref)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        async with self._state.db.session() as session:
            stored = await ModelParamsRepository(session).get(user_id, model_ref)
        params = msgspec.convert(stored, SamplingParams) if stored else None
        self._remember(key, params)
        return params

    async def put(self, user_id: str, model_ref: str, params: SamplingParams) -> SamplingParams:
        """Save parameters for a model, replacing what was there."""
        values = _compact(params)
        if not values:
            await self.clear(user_id, model_ref)
            return SamplingParams()
        async with self._state.db.write() as session:
            await ModelParamsRepository(session).put(user_id, model_ref, values)
        self._remember((user_id, model_ref), params)
        return params

    async def clear(self, user_id: str, model_ref: str) -> None:
        """Forget saved parameters for a model."""
        async with self._state.db.write() as session:
            await ModelParamsRepository(session).delete(user_id, model_ref)
        self._remember((user_id, model_ref), None)

    def _remember(self, key: tuple[str, str], params: SamplingParams | None) -> None:
        """Insert into the bounded cache, evicting the least recently used entry."""
        self._cache[key] = params
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_ENTRIES:
            self._cache.popitem(last=False)
