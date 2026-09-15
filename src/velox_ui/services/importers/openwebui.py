"""Import chats exported from Open WebUI (phase 9).

Open WebUI's own export (Settings -> Chats -> Export, or the admin "export all chats")
is a JSON array. Two shapes have been seen in the wild and both are accepted here:

* the modern one, where each entry wraps a ``chat`` object whose ``history.messages``
  is a dict keyed by message id, each node carrying ``parentId`` — the same tree shape
  velox-ui itself uses (ADR-0006), so the mapping is structural rather than a
  reinterpretation;
* the legacy one, where ``chat.messages`` is a flat, already-ordered list with no
  parent links, which is imported as a single linear branch.

This module is intentionally forgiving of the input — it is hand-exported JSON from
another project's database, not a contract either side versions — and skips a
malformed entry rather than aborting the whole run; every skip is reported back.

Nothing here talks to the database directly except through the existing chat, folder
and tag repositories, so an import behaves exactly like a person creating the same
chats by hand: same tables, same invariants, same indexes kept warm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import msgspec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from velox_ui.db.models import Chat
from velox_ui.db.repositories.chats import ChatRepository
from velox_ui.db.repositories.folders import FolderRepository
from velox_ui.db.repositories.tags import TagRepository

__all__ = ["ImportReport", "SkippedChat", "import_export", "parse_export"]

_SOURCE = "openwebui"

# Open WebUI model ids are backend-agnostic strings ("llama3:8b", "gpt-4o-mini"); velox
# model_ref is "{provider}:{model}". Cloud model families are recognised by prefix and
# routed to their provider id; anything else is assumed to be a local Ollama model,
# since that is Open WebUI's default and most common backend. This is a best-effort
# label on the imported chat, corrected by editing the model picker afterwards — it is
# never used to route a request itself, unlike a live chat's model_ref.
_CLOUD_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("chatgpt", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "gemini"),
    ("mistral-", "mistral"),
    ("mixtral-", "mistral"),
)


class SkippedChat(msgspec.Struct, frozen=True):
    """One export entry that was not imported."""

    source_id: str
    title: str
    reason: str


class ImportReport(msgspec.Struct, frozen=True):
    """The outcome of one import run."""

    imported: int
    skipped: tuple[SkippedChat, ...]


def parse_export(raw: bytes) -> list[dict[str, Any]]:
    """Parse an Open WebUI export file into a list of chat entries.

    Args:
        raw: The file's bytes, as downloaded from Open WebUI.

    Returns:
        The chat entries, regardless of which top-level shape the file used.

    Raises:
        ValueError: The file is not JSON, or not a recognisable export shape.
    """
    try:
        parsed = msgspec.json.decode(raw)
    except msgspec.DecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc

    if isinstance(parsed, list):
        entries = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("chats"), list):
        entries = parsed["chats"]
    else:
        raise ValueError(
            "expected a JSON array of chats, or an object with a 'chats' array "
            "(the two shapes Open WebUI's export has used)"
        )
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("every entry in the export must be a JSON object")
    return entries


async def import_export(
    session: AsyncSession,
    *,
    user_id: str,
    entries: Sequence[Mapping[str, Any]],
    folder_names: Mapping[str, str] | None = None,
) -> ImportReport:
    """Import a batch of Open WebUI chat entries for one user.

    Args:
        session: An open write session (caller commits — see :meth:`Database.write`).
        user_id: The velox-ui account the chats are imported into.
        entries: Parsed export entries, from :func:`parse_export`.
        folder_names: Open WebUI folder id -> name, for chats carrying a ``folder_id``.
            Open WebUI's own chat export does not include folder names, only ids, so
            this comes from a separate ``folders`` export when the caller has one;
            chats whose folder isn't in the map are imported unfiled rather than
            dropped or misfiled.

    Returns:
        How many chats were imported and which were skipped, with a reason each.

    Importing the same export twice is a no-op the second time: every created chat
    carries its source id in ``meta.import``, and an entry whose source id is already
    present for this user is skipped rather than duplicated.
    """
    chats = ChatRepository(session)
    folders = FolderRepository(session)
    tags = TagRepository(session)

    already_imported = await _imported_source_ids(session, user_id=user_id)
    folder_ids: dict[str, str] = {}
    tag_ids: dict[str, str] = {}
    skipped: list[SkippedChat] = []
    imported = 0

    for entry in entries:
        try:
            parsed = _parse_entry(entry)
        except ValueError as exc:
            skipped.append(
                SkippedChat(
                    source_id=str(entry.get("id", "?")),
                    title=str(entry.get("title", "") or entry.get("chat", {}).get("title", "")),
                    reason=str(exc),
                )
            )
            continue

        if parsed.source_id in already_imported:
            skipped.append(
                SkippedChat(
                    source_id=parsed.source_id,
                    title=parsed.title,
                    reason="already imported",
                )
            )
            continue

        folder_id: str | None = None
        if parsed.folder_source_id and folder_names:
            name = folder_names.get(parsed.folder_source_id)
            if name:
                folder_id = folder_ids.get(name)
                if folder_id is None:
                    folder = await folders.create(user_id=user_id, name=name)
                    await session.flush()
                    folder_id = folder.id
                    folder_ids[name] = folder_id

        chat = await chats.create(
            user_id=user_id,
            title=parsed.title,
            folder_id=folder_id,
            model_ref=parsed.model_ref,
            pinned=parsed.pinned,
            archived=parsed.archived,
            meta={"import": {"source": _SOURCE, "source_id": parsed.source_id}},
            created_at=parsed.created_at,
            updated_at=parsed.updated_at,
        )

        new_id_of: dict[str, str] = {}
        depth_of: dict[str, int] = {}
        active_leaf_id: str | None = None
        for node in parsed.nodes:
            parent_new_id = new_id_of.get(node.parent_id) if node.parent_id else None
            depth = 0 if parent_new_id is None else depth_of[parent_new_id] + 1
            message = await chats.append_message(
                chat_id=chat.id,
                parent_id=parent_new_id,
                role=node.role,
                content=node.content,
                status="complete",
                model_ref=node.model_ref or parsed.model_ref,
                depth=depth,
                created_at=node.created_at,
            )
            new_id_of[node.source_id] = message.id
            depth_of[message.id] = depth
            if node.source_id == parsed.current_source_id:
                active_leaf_id = message.id

        if active_leaf_id is None and new_id_of:
            # No usable currentId in the export: fall back to the last node written,
            # which for both shapes this module accepts is the most recent turn.
            active_leaf_id = next(reversed(new_id_of.values()))
        chat.active_leaf_id = active_leaf_id
        chat.message_count = len(new_id_of)

        for tag_name in parsed.tags:
            tag_id = tag_ids.get(tag_name)
            if tag_id is None:
                existing_tags = await tags.list_all(user_id=user_id)
                existing = next((t for t in existing_tags if t.name == tag_name), None)
                if existing is not None:
                    tag_id = existing.id
                else:
                    tag = await tags.create(user_id=user_id, name=tag_name)
                    await session.flush()
                    tag_id = tag.id
                tag_ids[tag_name] = tag_id
            await session.flush()
            await tags.attach(chat_id=chat.id, tag_id=tag_id, user_id=user_id)

        already_imported.add(parsed.source_id)
        imported += 1

    return ImportReport(imported=imported, skipped=tuple(skipped))


async def _imported_source_ids(session: AsyncSession, *, user_id: str) -> set[str]:
    """Return the source ids already imported for a user, read from ``chat.meta``.

    ``meta`` is opaque to SQL on SQLite (msgpack in a BLOB — see db/types.py), so this
    is a Python-side scan rather than a query. It runs once per import, over one
    user's chats, which is small next to the hot paths that scan is deliberately kept
    away from.
    """
    stmt = select(Chat.meta).where(Chat.user_id == user_id, Chat.deleted_at.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    ids: set[str] = set()
    for meta in rows:
        if not isinstance(meta, dict):
            continue
        imp = meta.get("import")
        if isinstance(imp, dict) and imp.get("source") == _SOURCE:
            source_id = imp.get("source_id")
            if isinstance(source_id, str):
                ids.add(source_id)
    return ids


class _Node(msgspec.Struct, frozen=True):
    """One message, still keyed by its Open WebUI id."""

    source_id: str
    parent_id: str | None
    role: str
    content: str
    model_ref: str | None
    created_at: int | None


class _ParsedChat(msgspec.Struct, frozen=True):
    """One export entry, normalized to what :func:`import_export` needs."""

    source_id: str
    title: str
    model_ref: str | None
    pinned: bool
    archived: bool
    tags: tuple[str, ...]
    folder_source_id: str | None
    created_at: int | None
    updated_at: int | None
    nodes: tuple[_Node, ...]
    current_source_id: str | None


def _parse_entry(entry: Mapping[str, Any]) -> _ParsedChat:
    """Normalize one export entry. Raises :class:`ValueError` for a malformed one."""
    source_id = entry.get("id")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("missing 'id'")

    chat_obj = entry.get("chat")
    chat_obj = chat_obj if isinstance(chat_obj, dict) else entry

    title = chat_obj.get("title") or entry.get("title") or "Untitled chat"
    if not isinstance(title, str):
        title = str(title)

    models = chat_obj.get("models")
    model_id = models[0] if isinstance(models, list) and models else chat_obj.get("model")
    model_ref = _model_ref(model_id) if isinstance(model_id, str) and model_id else None

    entry_meta = entry.get("meta")
    entry_meta = entry_meta if isinstance(entry_meta, dict) else {}
    tags_raw = chat_obj.get("tags") or entry_meta.get("tags") or []
    tags = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()

    folder_source_id = entry.get("folder_id")
    folder_source_id = folder_source_id if isinstance(folder_source_id, str) else None

    created_at = _normalize_timestamp(entry.get("created_at") or chat_obj.get("timestamp"))
    updated_at = _normalize_timestamp(entry.get("updated_at")) or created_at

    nodes, current_source_id = _parse_tree(chat_obj)
    if not nodes:
        raise ValueError("no messages")

    return _ParsedChat(
        source_id=source_id,
        title=title,
        model_ref=model_ref,
        pinned=bool(entry.get("pinned") or chat_obj.get("pinned")),
        archived=bool(entry.get("archived") or chat_obj.get("archived")),
        tags=tags,
        folder_source_id=folder_source_id,
        created_at=created_at,
        updated_at=updated_at,
        nodes=nodes,
        current_source_id=current_source_id,
    )


def _parse_tree(chat_obj: Mapping[str, Any]) -> tuple[tuple[_Node, ...], str | None]:
    """Read the message tree, preferring the modern ``history.messages`` shape."""
    history = chat_obj.get("history")
    messages_by_id = history.get("messages") if isinstance(history, dict) else None
    if isinstance(messages_by_id, dict) and messages_by_id:
        by_id = {
            mid: raw
            for mid, raw in messages_by_id.items()
            if isinstance(raw, dict) and isinstance(raw.get("role"), str)
        }
        ordered = sorted(by_id.values(), key=lambda m: m.get("timestamp") or 0)
        nodes = tuple(
            _Node(
                source_id=str(raw.get("id")),
                parent_id=raw.get("parentId") if isinstance(raw.get("parentId"), str) else None,
                role=_normalize_role(raw["role"]),
                content=_content_text(raw.get("content")),
                model_ref=_node_model_ref(raw),
                created_at=_normalize_timestamp(raw.get("timestamp")),
            )
            for raw in ordered
            if raw.get("id")
        )
        current = history.get("currentId") if isinstance(history, dict) else None
        return nodes, current if isinstance(current, str) else None

    flat = chat_obj.get("messages")
    if isinstance(flat, list) and flat:
        nodes = []
        previous_id: str | None = None
        for index, raw in enumerate(flat):
            if not isinstance(raw, dict) or not isinstance(raw.get("role"), str):
                continue
            source_id = str(raw.get("id") or index)
            nodes.append(
                _Node(
                    source_id=source_id,
                    parent_id=previous_id,
                    role=_normalize_role(raw["role"]),
                    content=_content_text(raw.get("content")),
                    model_ref=_node_model_ref(raw),
                    created_at=_normalize_timestamp(raw.get("timestamp")),
                )
            )
            previous_id = source_id
        return tuple(nodes), previous_id

    return (), None


def _normalize_role(role: str) -> str:
    """Map an Open WebUI role to one of velox-ui's four (docs/design/02-db-schema.md)."""
    return role if role in ("system", "user", "assistant", "tool") else "assistant"


def _content_text(content: Any) -> str:
    """Flatten Open WebUI content, which is a plain string except for vision turns."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def _normalize_timestamp(value: Any) -> int | None:
    """Normalize an Open WebUI timestamp (seconds, float seconds, or ms) to epoch ms."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number <= 0:
        return None
    # Open WebUI stores Unix seconds almost everywhere; anything already in the
    # millisecond range is left alone rather than multiplied again.
    if number < 10**12:
        number *= 1000
    return int(number)


def _node_model_ref(raw: Mapping[str, Any]) -> str | None:
    """``_model_ref`` for one message node, tolerating a missing/non-string model."""
    model_id = raw.get("model")
    return _model_ref(model_id) if isinstance(model_id, str) and model_id else None


def _model_ref(model_id: str) -> str:
    """Best-effort Open WebUI model id -> velox ``provider:model`` label.

    This never drives routing — it only labels the imported chat for display, and is
    corrected like any other chat by picking a model from the interface.
    """
    lowered = model_id.lower()
    for prefix, provider in _CLOUD_PREFIXES:
        if lowered.startswith(prefix):
            return f"{provider}:{model_id}"
    return f"ollama:{model_id}"
