"""A user's starred models.

Cold path: a handful of rows per user, changed by a click, so this uses Pydantic like
the other management routes.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.clock import now_ms
from velox_ui.db.models import FavoriteModel

router = APIRouter(prefix="/api/favorite-models", tags=["models"])


class FavoriteModelsResponse(BaseModel):
    """The caller's starred ``model_ref`` values."""

    model_refs: list[str]


class SetFavoriteRequest(BaseModel):
    """Payload for starring or un-starring one model."""

    model_ref: str = Field(min_length=1, max_length=255)
    favorite: bool


@router.get("", response_model=FavoriteModelsResponse, summary="List favorite models")
async def list_favorites(principal: CurrentPrincipal, state: State) -> FavoriteModelsResponse:
    """Return the caller's favorites, oldest first."""
    async with state.db.session() as session:
        refs = await session.scalars(
            select(FavoriteModel.model_ref)
            .where(FavoriteModel.user_id == principal.user_id)
            .order_by(FavoriteModel.created_at, FavoriteModel.model_ref)
        )
        return FavoriteModelsResponse(model_refs=list(refs))


@router.put("", response_model=FavoriteModelsResponse, summary="Star or un-star a model")
async def set_favorite(
    payload: SetFavoriteRequest, principal: CurrentPrincipal, state: State
) -> FavoriteModelsResponse:
    """Star or un-star one model; repeating either is a no-op.

    The reference is not checked against the live model list: providers can be offline,
    and a stale favorite is harmless.
    """
    async with state.db.write() as session:
        existing = await session.get(FavoriteModel, (principal.user_id, payload.model_ref))
        if payload.favorite and existing is None:
            session.add(
                FavoriteModel(
                    user_id=principal.user_id, model_ref=payload.model_ref, created_at=now_ms()
                )
            )
        elif not payload.favorite and existing is not None:
            await session.execute(
                delete(FavoriteModel).where(
                    FavoriteModel.user_id == principal.user_id,
                    FavoriteModel.model_ref == payload.model_ref,
                )
            )
        await session.flush()
        refs = await session.scalars(
            select(FavoriteModel.model_ref)
            .where(FavoriteModel.user_id == principal.user_id)
            .order_by(FavoriteModel.created_at, FavoriteModel.model_ref)
        )
        return FavoriteModelsResponse(model_refs=list(refs))
