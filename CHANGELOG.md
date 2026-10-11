# Changelog

## [Unreleased] — Phase 4: drafting + Markdown export (MVP)

### Added
- Context builder v1 with token budgeting, priority trimming and a context manifest per draft.
- Stage E drafting: continuation on truncation with overlap stripping, edge-only meta cleanup, draft checks.
- Name/alias consistency rule → evidence-linked Findings; `inkforge findings`.
- Approval of drafts (`approve drafts --scene/--chapter/--all`), preflight, Markdown export (final / watermarked preview), `diff`, `show`.
- `run --mode full` (plan → drafts → preview) and `--mode draft`.
- Offline acceptance test with a socket guard; 5-chapter novella golden fixture.

### Fixed
- Chaos test now kills the real worker process (Windows venv launcher) and proves it died.
- Craft rules are advisory (D32): one repair, then accepted with warnings — a real 1B run failed a whole job on a duplicate chapter purpose.
- Possessives no longer produce false name findings ("James's" was read as "Jame").
- Standalone timestamp lines are stripped from drafts; Codex names are trimmed.
- Sleep-safe model timeouts (awake time only) and keep-awake during jobs; `doctor` and jobs warn on battery (D33).

## [Unreleased] — Phase 3: planning pipeline (Stages A–D) + Conductor

### Added
- `inkforge.pipeline`: Stage base class; stages `spec` (with enhance + field diff, author fields enforced by code), `design` (acts, arcs, promises → registry, cast → Codex candidates), `chapters` (per act, stable chapter IDs, outline enhance, outline checks), `cards` (per chapter, ID enums, location resolution, value-charge and flashback placement checks, timeline).
- Conductor: resumable jobs with per-step checkpoints, approval gates, `--yes` for planning gates only (audited), cancel file, transient-error retry, `.run.lock`, crash repair (temp files, orphan versions → backups/orphans) and `verify_integrity()`.
- Proposal schemas with enums of allowed IDs (D27) and `min_length` text fields (D28).
- Prompts: `spec.generate@2`, `spec.enhance@1`, `design.generate@1`, `outline.act@1`, `outline.enhance@1`, `cards.chapter@1`.
- CLI: `spec`, `design`, `outline`, `cards`, `run --mode plan [--until] [--yes]`, `jobs`, `resume`, `cancel`, `approve`, `status`, `edit`, `codex`.
- Fake adapter auto mode (`SchemaExample`) and `INKFORGE_FAKE=auto` hook; chaos kill/resume test (20 kills, `-m slow`).

### Fixed
- Repetition guard now also catches long cycles (a 4-name loop in a list, 1B).
- `$EDITOR` with a quoted Windows path.

## [Unreleased] — Phase 2: model router & prompt registry

### Added
- `inkforge.models`: `ModelRouter` (profiles, allow-list, one generation in flight, unload-before-switch, load-error retry), adapters for Ollama, local OpenAI-compatible servers (llama.cpp `/tokenize`, LM Studio) and a Fake adapter (scripted, record/replay, simulated RAM limit).
- Streaming internally with first-token / idle / total timeouts and cooperative cancellation; cancelled text goes to `partial.txt` only.
- `generate_structured`: schema-constrained decoding, allowed-ID check, Pydantic validation, custom validators, repair attempt with the errors, temperature backoff, truncation-aware token growth.
- Repetition guard: stops degenerate loops (seen on llama3.2:1b) and repairs instead of waiting for max_tokens.
- Token counting chain: runtime tokenize → cached offline tokenizer → calibrated estimate.
- `inkforge.prompts`: versioned Jinja templates (sandboxed, StrictUndefined, project overrides) and SKILL.md skills (`three-act`, `save-the-cat`, `heros-journey`).
- Run recorder (`prompt.txt`, `raw_output.txt`, `meta.json`, `partial.txt`).
- CLI: `doctor` runtime checks, `prompts`, `bench` (mini-book through the real router).

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
