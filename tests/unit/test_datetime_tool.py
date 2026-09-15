"""The builtin date/time tool: it answers from the host clock, with no config."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from velox_ui.plugins.builtin.datetime_tools import DateTimeToolPlugin
from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import PluginConfig


def _plugin() -> DateTimeToolPlugin:
    # Deliberately an empty config: this plugin must work with nothing set up,
    # which is what lets the tools kind be enabled without a search backend.
    return DateTimeToolPlugin(PluginConfig(enabled=True), secrets=object())  # type: ignore[arg-type]


def test_offers_its_tool_with_no_configuration() -> None:
    tools = _plugin().tools()
    assert [tool.name for tool in tools] == ["current_datetime"]
    assert tools[0].parameters == {"type": "object", "properties": {}}


async def test_reports_the_current_date_and_time() -> None:
    answer = await _plugin().call("current_datetime", {})

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    assert today in answer
    assert datetime.now().astimezone().strftime("%A") in answer
    # Both the local zone and UTC, so the model cannot quietly assume either.
    assert "Time zone:" in answer
    assert "UTC:" in answer
    assert re.search(r"ISO 8601: \d{4}-\d{2}-\d{2}T", answer)


async def test_utc_line_is_actually_utc() -> None:
    answer = await _plugin().call("current_datetime", {})
    line = next(line for line in answer.splitlines() if line.startswith("UTC: "))
    reported = datetime.strptime(line.removeprefix("UTC: "), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=UTC
    )
    assert abs((datetime.now(UTC) - reported).total_seconds()) < 60


async def test_unknown_tool_raises() -> None:
    with pytest.raises(PluginUpstreamError, match="Unknown tool"):
        await _plugin().call("something_else", {})


async def test_validate_is_always_ok() -> None:
    result = await _plugin().validate()
    assert result.ok is True
