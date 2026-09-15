"""Plugin discovery and lazy loading (ADR-0014).

Discovery must never import a plugin's module — only :func:`load` does, and only for
an entry point a caller actually wants to use.
"""

from __future__ import annotations

import sys
from importlib import metadata

import pytest

from velox_ui.plugins.loader import EntryPointMeta, discover, load
from velox_ui.plugins.spec import PluginConfig

_FAKE_MODULE = "tests.fakes.fake_plugin"


def _fake_entry_points(group: str) -> tuple[metadata.EntryPoint, ...]:
    if group != "velox_ui.images":
        return ()
    return (
        metadata.EntryPoint(
            name="fake-images", value=f"{_FAKE_MODULE}:FakePlugin", group=group
        ),
    )


def test_discover_reads_metadata_without_importing(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop(_FAKE_MODULE, None)
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)

    found = discover("images")

    assert [e.name for e in found] == ["fake-images"]
    assert _FAKE_MODULE not in sys.modules


def test_discover_unknown_kind_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "entry_points", _fake_entry_points)
    assert discover("voice") == []


def test_load_imports_and_instantiates() -> None:
    sys.modules.pop(_FAKE_MODULE, None)
    entry_point = EntryPointMeta(
        name="fake-images", kind="images", value=f"{_FAKE_MODULE}:FakePlugin"
    )
    config = PluginConfig(enabled=True, base_url="http://localhost:9000")

    plugin = load(entry_point, config, secrets=object())  # type: ignore[arg-type]

    assert _FAKE_MODULE in sys.modules
    assert plugin.name == "fake"
    assert plugin.config is config  # type: ignore[attr-defined]
