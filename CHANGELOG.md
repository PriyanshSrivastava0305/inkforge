# Changelog

## [Unreleased] — Phase 0

### Added
- Repository skeleton: single `inkforge` package with component subpackages (ADR-0001), `uv` project, ruff, pyright, pytest, local pre-commit hooks.
- Config loader enforcing loopback-only runtime URL and model allow-list (D2/D16/D18); `inkforge version`, `inkforge profiles`.
- `config/model-profiles.toml` (8B default) and `model-profiles.fast.toml` (1B).
- Phase 0 scripts: Ollama smoke test, offline tokenizer builder, mini-book benchmark.
- Golden fixtures v0: `mini_book`, `continuity_book` outline.
- ADRs 0001–0002.

### Changed
- Plan moved from `plan/` to `docs/`; build guide and diagrams moved to `docs/reference/`.
