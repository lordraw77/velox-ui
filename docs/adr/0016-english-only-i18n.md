# ADR-0016: English is the source language

**Status:** proposed
**Date:** 2026-09-11

## Context
The project is developed in Italian but must be readable and contributable
internationally.

## Decision
Every artifact in the repository is English: UI strings, docs, docstrings, comments, log
and exception messages, CLI help, OpenAPI descriptions, identifiers, table and column
names, commit messages. UI strings are never hardcoded; they live in
`frontend/src/lib/i18n/en.json` as the source, with `it.json` as the first translation.
A CI check fails on non-ASCII-word heuristics in source strings and on any UI literal
that bypasses the i18n helper.

## Consequences
Contributors write in a second language; the lint check keeps drift out rather than
relying on review attention.
