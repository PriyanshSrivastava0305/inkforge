# InkForge

Local-first story and script writing agent — personal tool, strictly local (no network calls at runtime).

- **New here (or a Claude session picking this up)?** Read [`claude_context.md`](claude_context.md) first — concept, rules, architecture, status, next steps.
- **Plan & docs:** [`docs/`](docs/README.md) — start with `01`, then the roadmap in `10`. Architecture diagram: [`docs/inkforge-architecture.svg`](docs/inkforge-architecture.svg). Progress: [`docs/PROGRESS-REPORT.md`](docs/PROGRESS-REPORT.md).
- **Status:** Phases 0–6 done: plan → draft → long-book memory → check (continuity, craft, voice) → scoped revision, all offline. Next: full model evaluations on faster hardware, then Phase 7 (local web app). Sample output: [`examples/salt-letters`](examples/salt-letters/README.md). See [`CHANGELOG.md`](CHANGELOG.md).
- **Models (D16):** `llama3.1:8b-instruct-q4_K_M` (default), `llama3.2:1b` (fast tests), via Ollama on `127.0.0.1`.

## Setup

```bash
uv sync --extra tokenizer
uv run inkforge version
uv run pytest -m "not ollama"   # no model needed
uv run pytest                   # includes live Ollama checks (1B only)
```

`uv` lives in `%APPDATA%\Python\Python312\Scripts`; add that folder to PATH.

## CLI (so far)

```bash
uv run inkforge init "The Glass Orchard"      # creates ~/InkForge/workspace/projects/the-glass-orchard
uv run inkforge projects
uv run inkforge run -p the-glass-orchard --mode plan --idea "…"   # stops at each approval gate
uv run inkforge status -p the-glass-orchard
uv run inkforge approve -p the-glass-orchard spec && uv run inkforge resume -p the-glass-orchard
uv run inkforge codex -p the-glass-orchard --accept all
uv run inkforge draft -p the-glass-orchard                      # Stage E
uv run inkforge check -p the-glass-orchard                      # Stage F: rules, canon, craft, voice
uv run inkforge findings -p the-glass-orchard --accept find-0003
uv run inkforge revise -p the-glass-orchard                     # Stage G: fix accepted findings
uv run inkforge polish -p the-glass-orchard --chapter ch02      # check + fix med/high, pass by pass
uv run inkforge facts list -p the-glass-orchard                 # review extracted facts; accept/reject
uv run inkforge lessons list -p the-glass-orchard               # recurring findings → style rules
uv run inkforge approve -p the-glass-orchard drafts --all
uv run inkforge export -p the-glass-orchard                     # or --preview
uv run inkforge backup -p the-glass-orchard
uv run inkforge doctor
```

Add `--profile-set fast` to use `llama3.2:1b` for quick runs; `INKFORGE_FAKE=auto` runs everything on a fake model.

## Scripts (Phase 0)

| Script | Purpose |
|---|---|
| `scripts/smoke_ollama.py <model> [runs]` | Structured output, truncation, streaming, cancellation, model switching |
| `scripts/build_tokenizer.py` | Build the Llama-3 tokenizer from the local Ollama GGUF (offline) and verify it |
| `scripts/bench_models.py <model>` | Mini-book benchmark (`tests/golden/mini_book/`) |

## Layout

`src/inkforge/` — one package, subpackages per component (ADR-0001) · `config/` — TOML defaults and model profiles · `tests/` — unit / integration / golden / e2e · `docs/` — plan, ADRs, benchmarks, reference material.
