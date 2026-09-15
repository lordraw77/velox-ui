"""A fake plugin module, imported only by tests exercising the plugin loader.

Importing this module has a side effect (recording that it happened) so tests can
assert whether :func:`velox_ui.plugins.loader.load` was actually called.
"""

from __future__ import annotations

from velox_ui.plugins.spec import PluginConfig, PluginValidation

IMPORTED = True


class FakePlugin:
    """A minimal plugin used only to prove the loader's import/instantiate wiring."""

    name = "fake"

    def __init__(self, config: PluginConfig, *, secrets: object) -> None:
        """Record the config and secret box it was built with."""
        self.config = config
        self.secrets = secrets

    async def validate(self) -> PluginValidation:
        """Always report ok, for tests."""
        return PluginValidation(ok=True, detail="fake")
