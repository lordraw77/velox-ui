"""Full-text search across chat titles and message content.

Two physically different indexes back one dialect-neutral method: SQLite keeps
external-content FTS5 tables synced by triggers (``db/migrations``), PostgreSQL keeps
a generated ``tsvector`` column with a GIN index. Both are queried here behind
:meth:`SearchRepository.search`, so a route never branches on dialect.

Results are ordered by recency (``created_at`` descending, then id), not by relevance
score. The two dialects compute relevance differently (SQLite's bm25 and PostgreSQL's
``ts_rank`` are not comparable), and recency gives a keyset cursor that seeks cleanly
on both — the deliberate trade-off documented in the phase 6 report.
"""

from __future__ import annotations

import msgspec
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["SearchHit", "SearchPage", "SearchRepository"]


class SearchHit(msgspec.Struct, frozen=True):
    """One match, either a chat title or a message body."""

    kind: str  # "chat_title" | "message"
    chat_id: str
    chat_title: str
    message_id: str | None
    snippet: str
    created_at: int


class SearchPage(msgspec.Struct, frozen=True):
    """A page of search hits plus the cursor for the next one."""

    items: tuple[SearchHit, ...]
    next_cursor: tuple[int, str] | None


_SQLITE_MESSAGE_SQL = text(
    """
    SELECT m.id, m.chat_id, c.title, m.created_at,
           snippet(message_fts, 0, '[', ']', '…', 10) AS snip
    FROM message_fts
    JOIN message m ON m.rowid = message_fts.rowid
    JOIN chat c ON c.id = m.chat_id
    WHERE message_fts MATCH :query
      AND c.user_id = :user_id AND c.deleted_at IS NULL
      AND (m.created_at, m.id) < (:cursor_ts, :cursor_id)
    ORDER BY m.created_at DESC, m.id DESC
    LIMIT :limit
    """
)

_SQLITE_CHAT_SQL = text(
    """
    SELECT c.id, c.title, c.updated_at,
           snippet(chat_fts, 0, '[', ']', '…', 10) AS snip
    FROM chat_fts
    JOIN chat c ON c.rowid = chat_fts.rowid
    WHERE chat_fts MATCH :query
      AND c.user_id = :user_id AND c.deleted_at IS NULL
      AND (c.updated_at, c.id) < (:cursor_ts, :cursor_id)
    ORDER BY c.updated_at DESC, c.id DESC
    LIMIT :limit
    """
)

_POSTGRES_MESSAGE_SQL = text(
    """
    SELECT m.id, m.chat_id, c.title, m.created_at,
           ts_headline('english', m.content, plainto_tsquery('english', :raw_query)) AS snip
    FROM message m
    JOIN chat c ON c.id = m.chat_id
    WHERE m.tsv @@ plainto_tsquery('english', :raw_query)
      AND c.user_id = :user_id AND c.deleted_at IS NULL
      AND (m.created_at, m.id) < (:cursor_ts, :cursor_id)
    ORDER BY m.created_at DESC, m.id DESC
    LIMIT :limit
    """
)

_POSTGRES_CHAT_SQL = text(
    """
    SELECT c.id, c.title, c.updated_at,
           ts_headline('english', c.title, plainto_tsquery('english', :raw_query)) AS snip
    FROM chat c
    WHERE c.tsv @@ plainto_tsquery('english', :raw_query)
      AND c.user_id = :user_id AND c.deleted_at IS NULL
      AND (c.updated_at, c.id) < (:cursor_ts, :cursor_id)
    ORDER BY c.updated_at DESC, c.id DESC
    LIMIT :limit
    """
)


def _sqlite_match_query(raw: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Each whitespace-separated term becomes a quoted phrase (escaping embedded quotes
    by doubling them, FTS5's own convention), joined with ``AND``. This treats FTS5
    query syntax characters in the user's input as literal text instead of raising a
    syntax error on them.
    """
    terms = [term for term in raw.split() if term]
    if not terms:
        return '""'
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


class SearchRepository:
    """Searches chat titles and message content for one user.

    Args:
        session: The session to operate in.
        is_sqlite: Which dialect's index to query.
    """

    __slots__ = ("_is_sqlite", "_session")

    def __init__(self, session: AsyncSession, *, is_sqlite: bool) -> None:
        """Bind the repository to a session and a dialect."""
        self._session = session
        self._is_sqlite = is_sqlite

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        cursor: tuple[int, str] | None = None,
        limit: int = 20,
    ) -> SearchPage:
        """Return one page of matches, newest first, merged across titles and messages.

        Args:
            user_id: Owner whose chats are searched.
            query: Free-text query.
            cursor: ``(created_at, id)`` of the last hit of the previous page.
            limit: Page size, already clamped by the route.

        Returns:
            The merged, re-paginated page and the cursor for the next one.
        """
        query = query.strip()
        if not query:
            return SearchPage(items=(), next_cursor=None)

        cursor_ts, cursor_id = cursor if cursor is not None else (2**62, "￿")
        # Over-fetch each side so merging still yields `limit` hits after interleaving.
        fetch = limit + 1
        params = {
            "user_id": user_id,
            "cursor_ts": cursor_ts,
            "cursor_id": cursor_id,
            "limit": fetch,
        }

        if self._is_sqlite:
            message_params = {**params, "query": _sqlite_match_query(query)}
            chat_params = {**params, "query": _sqlite_match_query(query)}
            message_rows = (
                await self._session.execute(_SQLITE_MESSAGE_SQL, message_params)
            ).all()
            chat_rows = (await self._session.execute(_SQLITE_CHAT_SQL, chat_params)).all()
        else:
            message_params = {**params, "raw_query": query}
            chat_params = {**params, "raw_query": query}
            message_rows = (
                await self._session.execute(_POSTGRES_MESSAGE_SQL, message_params)
            ).all()
            chat_rows = (await self._session.execute(_POSTGRES_CHAT_SQL, chat_params)).all()

        hits: list[SearchHit] = []
        for row in message_rows:
            hits.append(
                SearchHit(
                    kind="message",
                    chat_id=row.chat_id,
                    chat_title=row.title,
                    message_id=row.id,
                    snippet=row.snip,
                    created_at=row.created_at,
                )
            )
        for row in chat_rows:
            hits.append(
                SearchHit(
                    kind="chat_title",
                    chat_id=row.id,
                    chat_title=row.title,
                    message_id=None,
                    snippet=row.snip,
                    created_at=row.updated_at,
                )
            )

        def _row_id(hit: SearchHit) -> str:
            return hit.message_id if hit.message_id is not None else hit.chat_id

        hits.sort(key=lambda hit: (hit.created_at, _row_id(hit)), reverse=True)
        page = hits[:limit]
        has_more = len(hits) > limit
        next_cursor = (page[-1].created_at, _row_id(page[-1])) if has_more and page else None
        return SearchPage(items=tuple(page), next_cursor=next_cursor)


def clamp_search_limit(limit: int | None, *, default: int = 20, maximum: int = 50) -> int:
    """Clamp a client-supplied search page size."""
    if limit is None:
        return default
    return max(1, min(limit, maximum))
