"""Search across chat titles and message content.

Cold path, like the other management routes: a search is a deliberate action, not
something that runs on every keystroke of a completion turn.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.api.pagination import decode_cursor, encode_cursor
from velox_ui.db.repositories.search import SearchRepository, clamp_search_limit

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchHitResponse(BaseModel):
    """One match, either a chat title or a message body."""

    kind: str
    chat_id: str
    chat_title: str
    message_id: str | None
    snippet: str
    created_at: int


class SearchResponse(BaseModel):
    """A page of search hits."""

    items: list[SearchHitResponse]
    next_cursor: str | None


@router.get("", response_model=SearchResponse, summary="Search chats and messages")
async def search(
    principal: CurrentPrincipal,
    state: State,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> SearchResponse:
    """Search the caller's chat titles and message content.

    Raises:
        ValidationError: If the cursor is malformed.
    """
    decoded = decode_cursor(cursor, arity=2) if cursor else None
    typed_cursor = (int(decoded[0]), str(decoded[1])) if decoded else None
    async with state.db.session() as session:
        page = await SearchRepository(session, is_sqlite=state.db.is_sqlite).search(
            user_id=principal.user_id,
            query=q,
            cursor=typed_cursor,
            limit=clamp_search_limit(limit),
        )
    return SearchResponse(
        items=[
            SearchHitResponse(
                kind=hit.kind,
                chat_id=hit.chat_id,
                chat_title=hit.chat_title,
                message_id=hit.message_id,
                snippet=hit.snippet,
                created_at=hit.created_at,
            )
            for hit in page.items
        ],
        next_cursor=encode_cursor(page.next_cursor) if page.next_cursor else None,
    )
