# ADR-0003: SQLite (WAL) by default, PostgreSQL optional

**Status:** proposed
**Date:** 2026-09-11

## Context
"`docker run` and it works" forbids a mandatory external database. Postgres must stay
available for multi-user and multi-replica deployments.

## Decision
SQLAlchemy 2.0 async with aiosqlite by default (WAL, `synchronous=NORMAL`, a single
writer connection plus a small reader pool) and asyncpg as an option. Dialect-specific
features are confined to `db/fts/` (FTS5 vs tsvector) and `rag/store/` (sqlite-vec vs
pgvector). Alembic migrations run automatically at startup unless disabled.

## Consequences
Two search implementations and two vector stores to maintain and test. Multi-process
deployments require Postgres; this is documented rather than hidden. SQLite write
concurrency is serialized, which is acceptable because chat writes are small, batched
and off the critical path (ADR-0005).
