"""Shared field types for request models.

The email type here is deliberately permissive. A strict RFC validator with
deliverability checks rejects ``user@homelab.local`` and every other internal domain,
which is exactly the environment this application is built for: a self-hosted instance
on a home or office network, often with no public DNS at all. Refusing those addresses
would block legitimate sign-ups to protect against a typo, so the check is limited to
what actually matters — a single ``@``, no whitespace, something on both sides, and a
bounded length.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

__all__ = ["DisplayName", "Email", "Password"]

_EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s]{1,255}$"

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=_EMAIL_PATTERN),
    Field(description="Email address. Internal domains such as .local are accepted."),
]

Password = Annotated[str, Field(min_length=10, max_length=1024)]

DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
