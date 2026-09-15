"""Builtin date and time tool.

A language model has no clock — its only notion of "now" is whenever its training
data ended, which is usually months or years stale and which it will nonetheless
state confidently. This hands it the real one.

Registered under the ``velox_ui.tools`` entry-point group alongside the websearch
plugin (ADR-0014). It needs no configuration and no backend: unlike every other
plugin in the project it answers from the host itself, so it offers its tool as
soon as the ``tools`` plugin kind is enabled, whether or not a search backend has
been configured.

The package is ``datetime_tools`` rather than ``datetime`` so it cannot shadow the
standard library module of that name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from velox_ui.plugins.errors import PluginUpstreamError
from velox_ui.plugins.spec import PluginConfig, PluginValidation, ToolDefinition
from velox_ui.security.crypto import SecretBox

__all__ = ["DateTimeToolPlugin"]


class DateTimeToolPlugin:
    """Reports the host's current date and time."""

    name = "builtin-datetime"

    def __init__(self, config: PluginConfig, *, secrets: SecretBox) -> None:
        """Take the standard plugin arguments; this plugin needs neither."""
        del config, secrets

    def tools(self) -> list[ToolDefinition]:
        """Describe ``current_datetime``. Always available — there is nothing to configure."""
        return [
            ToolDefinition(
                name="current_datetime",
                description=(
                    "Get the current date and time. Use this whenever the answer "
                    "depends on what day or time it is now, rather than guessing."
                ),
                parameters={"type": "object", "properties": {}},
            )
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Answer ``current_datetime``.

        Raises:
            PluginUpstreamError: If asked for a tool this plugin does not provide.
        """
        del arguments
        if name != "current_datetime":
            raise PluginUpstreamError(f"Unknown tool {name!r}.")

        # Local time first, since that is what a person asking "what day is it"
        # means, with the zone named so the model cannot silently assume UTC —
        # and UTC alongside it so the answer is unambiguous either way.
        local = datetime.now().astimezone()
        return "\n".join(
            [
                f"Local time: {local.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Day of week: {local.strftime('%A')}",
                f"Time zone: {local.tzname()} (UTC{local.strftime('%z')})",
                f"UTC: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}",
                f"ISO 8601: {local.isoformat()}",
            ]
        )

    async def validate(self) -> PluginValidation:
        """Always healthy: the answer comes from the host's own clock."""
        local = datetime.now().astimezone()
        return PluginValidation(
            ok=True, detail=f"Host clock reads {local.strftime('%Y-%m-%d %H:%M:%S %Z')}."
        )
