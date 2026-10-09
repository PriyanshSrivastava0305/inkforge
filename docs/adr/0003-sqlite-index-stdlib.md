# ADR-0003: Project index on stdlib `sqlite3`, rebuilt on schema change (no SQLModel / Alembic)

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** D3 (files are the source of truth), docs/04 § SQLite index / Migrations

## Context

`03`/`04` proposed SQLModel tables with Alembic migrations. The index holds nothing that isn't derivable from project files, and `04` already says a failed migration can fall back to a reindex.

## Decision

- `src/inkforge/storage/index.py` uses stdlib `sqlite3` with a plain DDL string and a `meta.schema_version` row.
- If the file is missing, corrupt or has another schema version, it is **rebuilt from files** automatically. There are no migrations.
- Writes refresh only the rows affected by the change (`ProjectIndex.refresh`). `rebuild()` and `refresh()` share the same row builders, and a test asserts that an incrementally maintained index equals a fresh rebuild.
- FTS5 virtual tables: `scene_text_fts` (active drafts) and `codex_fts`.

## Alternatives considered

| Option | Why not |
|---|---|
| SQLModel + Alembic | Migration machinery for a disposable cache; two extra dependencies |
| No index at all (scan files) | Phase 5 retrieval needs FTS5; queries for the UI need joins |

## Consequences

- Changing the index schema means bumping `SCHEMA_VERSION`; every project silently rebuilds on next open.
- File schemas still migrate lazily via their `schema` field (docs/04 § Migrations).
