"""velox-ui: a fast, self-hosted frontend for local and remote LLMs.

This module is imported by every entry point, so it deliberately contains nothing
but metadata. Keeping it free of side effects and heavy imports is what makes the
sub-second cold start achievable (see ADR-0001 and docs/design/01-repo-layout.md).
"""

__version__ = "0.1.2"

__all__ = ["__version__"]
