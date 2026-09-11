# ADR-0013: Provider credentials encrypted at rest

**Status:** proposed
**Date:** 2026-09-11

## Context
API keys sit in the same SQLite file as the chats, which users back up and copy around.

## Decision
Secrets live in a dedicated `secret` table, encrypted with AES-GCM using a key derived
from `VELOX_SECRET_KEY` (auto-generated into the data directory on first run if unset).
The API never returns a secret, only a masked hint. A logging filter redacts known secret
values and `Authorization` headers from every log record and from upstream error bodies
before they are stored or returned.

## Consequences
Losing `VELOX_SECRET_KEY` means re-entering provider keys; this is documented in the
backup guide. Encryption is not a defence against an attacker who already has the host,
and the README says so plainly.
