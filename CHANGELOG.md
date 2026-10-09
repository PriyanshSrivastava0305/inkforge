# Changelog

## [Unreleased] — Phase 1: domain contracts & workspace storage

### Added
- `inkforge.domain`: Pydantic records for every entity in docs/04 (schema-versioned files, strict extra-field rejection), stable prefixed IDs, form profiles (novel, short-story; script forms stubbed).
- `inkforge.storage`: versioned artifacts with active/approved pointers and input hashes; staleness (hard inputs → stale, neighbour context → review); atomic writes; JSONL tables; SQLite index with FTS5, incrementally refreshed and rebuildable (ADR-0003); backup/restore with hash manifest.
- `inkforge.policy`: path guard (traversal, absolute/UNC, reserved names, ADS, symlinks, NTFS junctions), cross-process project lock, append-only audit log.
- CLI: `init`, `projects`, `reindex`, `backup`, `restore`, `doctor`. UTF-8 console output on Windows.
- 153 tests (unit, property-based, integration).

## Phase 0 — benchmark results
- Mini-book benchmark on both models; ADR-0002 confirms the 8B default / 1B plumbing split.
- Benchmark quote matcher folds quote marks and dashes (8B swaps `"` for `'` in JSON).

## Phase 0

### Added
- Repository skeleton: single `inkforge` package with component subpackages (ADR-0001), `uv` project, ruff, pyright, pytest, local pre-commit hooks.
- Config loader enforcing loopback-only runtime URL and model allow-list (D2/D16/D18); `inkforge version`, `inkforge profiles`.
- `config/model-profiles.toml` (8B default) and `model-profiles.fast.toml` (1B).
- Phase 0 scripts: Ollama smoke test, offline tokenizer builder, mini-book benchmark.
- Golden fixtures v0: `mini_book`, `continuity_book` outline.
- ADRs 0001–0002.

### Changed
- Plan moved from `plan/` to `docs/`; build guide and diagrams moved to `docs/reference/`.
