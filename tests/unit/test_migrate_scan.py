"""The Alembic-free head detection.

The fast path exists to keep Alembic off the startup import graph. These tests check
both that it finds the head and that it refuses to guess when it cannot.
"""

from __future__ import annotations

import sys

from velox_ui.db import migrate


def test_head_is_found_without_importing_alembic(monkeypatch) -> None:
    for module in [name for name in sys.modules if name.startswith("alembic")]:
        monkeypatch.delitem(sys.modules, module, raising=False)
    head = migrate.head_revision()
    assert head, "the migration history must have exactly one head"
    assert not any(name.startswith("alembic") for name in sys.modules), (
        "head detection must not import alembic; that import is a tenth of the "
        "cold-start budget"
    )


def test_branched_history_refuses_to_guess(monkeypatch) -> None:
    monkeypatch.setattr(migrate, "_scan_script_graph", lambda: ({"a", "b"}, set()))
    assert migrate.head_revision() is None, "two heads must fall back to alembic"


def test_unparsable_scripts_refuse_to_guess(monkeypatch) -> None:
    monkeypatch.setattr(migrate, "_scan_script_graph", lambda: None)
    assert migrate.head_revision() is None
