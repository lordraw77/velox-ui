"""The incremental SSE parser both HTTP transports read their streams with."""

from __future__ import annotations

from collections.abc import AsyncIterator

from velox_ui.mcp.sse_events import SseEvent, iter_sse_events, parse_json_rpc


async def _lines(*lines: str) -> AsyncIterator[str]:
    for line in lines:
        yield line


async def _events(*lines: str) -> list[SseEvent]:
    return [event async for event in iter_sse_events(_lines(*lines))]


async def test_named_and_unnamed_events() -> None:
    events = await _events(
        "event: endpoint", "data: /messages/?session_id=1", "", 'data: {"id": 1}', ""
    )
    assert events == [
        SseEvent(event="endpoint", data="/messages/?session_id=1"),
        # No `event` field means "message", as the SSE specification defines.
        SseEvent(event="message", data='{"id": 1}'),
    ]


async def test_comments_such_as_keepalive_pings_are_skipped() -> None:
    events = await _events(": ping - 2026-09-17", "", "data: x", "", ": ping", "")
    assert events == [SseEvent(event="message", data="x")]


async def test_multiline_data_is_joined_with_newlines() -> None:
    assert await _events("data: first", "data: second", "") == [
        SseEvent(event="message", data="first\nsecond")
    ]


async def test_carriage_returns_and_a_missing_space_are_tolerated() -> None:
    assert await _events("event:endpoint\r", "data:/m\r", "\r") == [
        SseEvent(event="endpoint", data="/m")
    ]


async def test_an_event_without_its_closing_blank_line_is_still_delivered() -> None:
    assert await _events("data: last") == [SseEvent(event="message", data="last")]


async def test_a_blank_line_with_no_data_dispatches_nothing() -> None:
    assert await _events("event: endpoint", "", "") == []


def test_parse_json_rpc_accepts_objects_only() -> None:
    assert parse_json_rpc('{"id": 1}') == {"id": 1}
    assert parse_json_rpc(b'{"id": 1}') == {"id": 1}
    assert parse_json_rpc("[1, 2]") is None
    assert parse_json_rpc("/messages/?session_id=1") is None
    assert parse_json_rpc(b"\xff") is None
