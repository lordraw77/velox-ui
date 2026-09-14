"""MCP (Model Context Protocol) clients and server management.

Imported lazily, from ``AppState.mcp`` on first use or from a route handler, never
from the default cold-start import graph (docs/design/01-repo-layout.md,
"Import-cost rule"). A chat with no MCP servers configured never imports this package.
"""

from __future__ import annotations
