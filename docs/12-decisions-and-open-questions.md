# 12 — Decisions & Open Questions

## Decision log

Lightweight ADRs. Status: **Accepted** (decided here, based on the build guide), **Proposed** (my recommendation, needs your OK), **Open** (needs your call).

| # | Decision | Status | Rationale |
|---|---|---|---|
| D1 | Python backend (FastAPI, Typer, Pydantic) + React/TS frontend | Accepted | Build guide; ML tooling; matches your stack |
| D2 | Local-only runtime, loopback binding, no cloud fallback | Accepted | Build guide non-negotiable |
| D3 | Files (JSON/Markdown) are source of truth; SQLite + indexes rebuildable | Accepted | Portability, diffability, recovery |
| D4 | Data contracts + checkpointed pipeline before any UI | Accepted | Build guide §15 |
| D5 | Ollama first, llama.cpp second, generic local OpenAI-compatible third | Accepted | Ollama already installed; llama.cpp gives tokenize + grammars |
| D6 | IDs minted by code; model may only reference provided IDs | Proposed | Prevents hallucinated entities and path tricks |
| D7 | Explicit `order` field; IDs never renumbered | Proposed | Stable references across reorders |
| D8 | Pointer JSON files instead of symlinks for active/approved versions | Proposed | Windows compatibility |
| D9 | Form-aware domain from Phase 1; script mode delivered in Phase 9 | Proposed | You asked for story **and** script; avoids a later rewrite |
| D10 | Scripts written as Fountain plain text; PDF rendered by our exporter | Proposed | Diffable, model-friendly, deterministic formatting |
| D11 | Facts extracted by model only enter canon via review queue, with verbatim-quote verification | Proposed | Author owns canon; blocks hallucinated evidence |
| D12 | Revision keeps a candidate only if it measurably improves (AuthorAgent/GEPA idea) | Proposed | Stops revision loops making text worse |
| D13 | Memory consolidation is on-demand, not a nightly scheduler | Proposed | Local single-user app; simpler |
| D14 | Fake model adapter with record/replay as the default test backend | Proposed | Fast, deterministic CI without a GPU |
| D15 | User workspace outside repo at `%USERPROFILE%\InkForge\workspace` | Proposed | Keeps manuscripts out of source control |
| D16 | Models: `llama3.1:8b-instruct-q4_K_M` default for every role; `llama3.2:1b` for fast real-model plumbing tests; Fake adapter for CI. No other models pulled | **Accepted** (2026-10-09) | Already installed; smoke-tested 2026-10-09: valid structured JSON + `length` signal on both ([08](08-model-runtime.md#profiles)) |
| D17 | Personal tool for your own use only: no distribution, installers, multi-user, licensing work | **Accepted** (2026-10-09) | Your call. Shrinks Phase 11; no need to clean up reference-code licensing beyond "ideas only" |
| D18 | Strictly local; no opt-in cloud provider, now or later | **Accepted** (2026-10-09) | Your call (closes Q1). Reinforces D2 |
| D19 | Item (object) entity in Codex; item state (holder/location/condition) as time-valid facts | Accepted | Object-state rules need it ([04](04-data-model.md#item-codex--objects-that-matter-to-continuity)) |
| D20 | Separate narrative order (outline) from story order (`timeline.json`); all temporal rules use story order; fact validity bounds are scene refs | Accepted | One ordering for rules; flashbacks work ([04](04-data-model.md#story-time)) |
| D21 | One versioned file per ChapterPlan; staleness from declared input hashes only | Accepted | Editing one chapter mustn't stale the whole book |
| D22 | `canon/promises.json` is the only promise store; designs/cards reference IDs | Accepted | Removes duplicate source of truth |
| D23 | `--yes` answers planning gates A–D only; never approves scenes, canon or final export. `export --preview` gives a watermarked draft export | Accepted (closes Q7) | Keeps "nothing exported without approval" true while allowing overnight runs ([05](05-pipeline.md#approval--yes)) |
| D24 | Phase 7 split: 7a (API + authoring views, after Phase 4), 7b (quality views, after Phase 6) | Accepted | Quality views need the Phase 6 engine |
| D25 | Context render order is stable-first for Ollama prefix-cache reuse, independent of trimming priority | Accepted | CPU prompt reading is ~10 tok/s cold vs ~170 cached ([06](06-memory-and-context.md#render-order--priority-order-prefix-caching)) |

## Open questions (need your call)

Closed: **Q1** → D18 (strictly local) · **Q5** → measured: i7-1165G7, 16 GB RAM, CPU-only (D16) · **Q6** → D17 (personal) · **Q7** → D23 · **Q3** → moot under D17: no license needed for personal use; OpenWrite (AGPL) and GOAT stay ideas-only, AuthorAgent (MIT) code may be adapted with attribution.

| # | Question | Default if you don't decide | Needed by |
|---|---|---|---|
| Q2 | Which forms matter most for script mode: feature screenplay, TV episode, stage play, or something else (comics, game dialogue)? | Feature screenplay + TV episode | Phase 9 (but affects Phase 1 profiles) |
| Q4 | Name: keep **InkForge**? | Keep | Phase 0 |
| Q10 | Should hosting be on git (local repo only, or also a private GitHub remote for CI)? | Local git + pre-commit; no remote CI | Phase 0 |
| Q8 | Import existing manuscripts (DOCX/Markdown) for polish-only use? | Yes, Markdown in Phase 6, DOCX in Phase 10 | Phase 6 |
| Q9 | Languages other than English (Hindi, etc.)? Affects lint rules, tokenization estimates, model choice | English only for v1 | Phase 6 |

## Known risks

| Risk | Impact | Mitigation |
|---|---|---|
| Local model prose quality is mediocre (8B ceiling) | You lose interest | Strong planning + critique loop; prose profile tuning; author edits are first-class; lessons |
| CPU-only speed (~3 tok/s on 8B) | A novel draft takes multiple nights; slow iteration | Small context, prefix-cache-friendly prompts, per-chapter runs, checkpoints/resume, 1B profile set for plumbing tests, Fake adapter for everything else |
| RAM pressure (16 GB; ~0.8 GB free with 8B loaded) | Model load fails (seen: HTTP 500), web UI + model contend | One model loaded at a time, explicit unload on switch, clear OOM errors, `doctor` reports free RAM, Vite dev server only when needed |
| Structured output failures on small models | Pipeline stalls | Constrained decoding, small schemas, repair retries, Fake tests for malformed output |
| Model critics noisy | Author fatigue | Rules first; evidence verification; advisory severities; calibration set |
| Scope creep (UI, publishing features) | Never ship | Milestones with exit criteria; publishing suite explicitly out of scope |
| Windows-specific FS issues (locks, junctions, long paths) | Data corruption | Tests run on Windows from Phase 0 (your machine is the target); path guard tests; atomic writes |
| Long runs on slow hardware | Hours per book | Checkpoints, resume, per-chapter runs, run console ETA |
