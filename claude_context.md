# claude_context.md — read this first

Handoff for any Claude session picking up **InkForge**. It says what the project is, the rules that must not be broken, how the code is laid out, what is done, what was learned the hard way, and what to do next — especially now that the work moves to a **Blackwell GPU machine**. Last updated 2026-10-28.

---

## 1. What InkForge is

A **local-first story and script writing agent**. The author gives a one-line idea; a staged, checkpointed pipeline turns it into a planned, drafted, continuity-checked, revised manuscript. Prose forms (novel, novella, short story) work today. Script forms (screenplay, stage play) are designed for but not built (Phase 9).

**The one idea behind the whole design: the model proposes, code verifies, the author decides.**

- A local LLM only produces *proposals* (structured JSON or prose).
- Deterministic code validates, stores and gates every proposal. It mints all IDs, checks that every quote exists in the text, runs rules, and proves edits stayed in bounds.
- Nothing becomes canon, approved, or final without an explicit human action, and every such action is audited.

It is a **personal tool for one author**: no accounts, no multi-user, no distribution work (decision D17).

## 2. Non-negotiables (do not relax these without the owner saying so)

1. **Strictly local.** No cloud model, no cloud fallback, no telemetry, ever (D2/D18). The runtime address must be loopback (`127.0.0.1`); config validation rejects anything else. Tests run a socket guard and assert zero non-loopback connection attempts.
2. **Model allow-list.** Only models in `config/model-profiles.toml` → `[runtime].allowed_models` may be referenced; config load fails otherwise (D16). Today that list is `llama3.1:8b-instruct-q4_K_M`, `llama3.2:1b` and `nomic-embed-text` (embeddings, disabled). **See §8: this list was set for a 16 GB CPU laptop and is expected to grow on the new machine — grow it deliberately, still local-only.**
3. **The author decides.** Drafts are never auto-approved. Extracted facts reach the review queue only; `inkforge facts accept` is the only path to canon. `--yes` approves planning gates only (spec/design/outline/scene_cards), never drafts or canon (D23).
4. **Files are the source of truth** (D3). Everything is versioned (`<name>.vN.<ext>` + `<name>.meta.json`), written atomically, and recoverable. SQLite files (`index.sqlite`, `recall.sqlite`) are derived and rebuildable.
5. **Every finding needs evidence.** A finding without a verified quote must be marked `is_hypothesis`, and hypotheses are never revised automatically.
6. **Revision is scoped and provable.** Stage G may only change one paragraph window per edit, and a word-diff guard proves nothing else changed.

## 3. Quick start

```bash
uv sync --extra tokenizer          # Python 3.12, uv
uv run inkforge version
uv run pytest -m "not ollama and not slow"      # 375 fast tests, no model needed
uv run python scripts/try_agent.py fake         # whole agent end to end in ~20 s, prints PASS/FAIL
uv run python scripts/try_agent.py fast --keep  # same on llama3.2:1b (~40 min on the CPU laptop)
uv run python scripts/try_agent.py default --keep   # same on the default 8B profile
```

- `INKFORGE_FAKE=auto` makes any CLI command use the scripted fake model (used by tests and the 20-second trial).
- `--profile-set fast` selects `config/model-profiles.fast.toml` (the 1B).
- User data lives outside the repo: `~/InkForge/workspace/projects/<project>/` (D15). Trial projects with `--keep` go to `~/InkForge/trials/`.
- Windows note: `uv` may not be on PATH. It was at `%APPDATA%\Python\Python312\Scripts`.

## 4. Repository map

```
src/inkforge/            single package (ADR-0001)
  cli.py cli_pipeline.py cli_quality.py     Typer commands (+ rich output)
  config.py                                 TOML config; loopback + allow-list validation
  system.py                                 awake clock, keep-awake, battery warning
  domain/        models.py (all entities), ids.py, forms.py (+ forms/*.toml), codex.py
  storage/       workspace.py (ProjectStore), versioned.py, atomic.py, tables.py (JSONL),
                 index.py (SQLite+FTS), backup.py, hashing.py
  policy/        pathguard.py, lock.py (OS file locks), audit.py
  models/        router.py (ModelRouter), tokens.py, allowed.py, recorder.py, health.py,
                 adapters/{ollama, openai_local, fake, base}.py
  prompts/       registry.py, skills.py, templates/<id>/<name>@<version>.md.j2, skills/
  pipeline/      stage.py (Stage base), conductor.py, proposals.py, blocks.py, text.py,
                 stages/{spec, design, chapters, cards, draft, critique, revise, export}.py
  context/       builder.py   token-budgeted draft prompt (builder v2)
  memory/        effective.py, digests.py, core.py, recall.py, service.py
  quality/       rules.py (canon conflict rules), extract.py (fact extraction + quote checks),
                 check.py, critics.py, deterministic.py, revision.py, canon.py,
                 report.py, lessons.py, names.py
  exporters/     markdown.py, preflight.py
config/          defaults.toml, model-profiles.toml (default), model-profiles.fast.toml (1B)
docs/            01..12 plan docs, adr/, benchmarks/, reference/ (build guide + diagrams),
                 PROGRESS-REPORT.md, inkforge-architecture.svg
tests/           unit/, integration/, golden/ (fixture books), support/
scripts/         try_agent.py, eval_continuity.py, bench_models.py, smoke_ollama.py, build_tokenizer.py
examples/salt-letters/   a real 8B output kept as an honest sample (no human edits)
```

Docs worth reading, in order: `docs/01` (vision), `docs/03` (architecture), `docs/05` (pipeline), `docs/06` (memory), `docs/07` (quality), `docs/08` (model runtime), `docs/10` (roadmap), `docs/12` (decision log D1–D37), `docs/inkforge-architecture.svg` (diagram). `docs/README.md` indexes them.

## 5. How it works

**Conductor** (`pipeline/conductor.py`) runs a *job* = a list of stages; each stage expands into steps (one per scene/chapter). `runs/<job>/job.json` is rewritten after every step (the checkpoint). It supports resume, cancel (cancel file + token), one retry on transient model errors, crash repair, an integrity check, a project run-lock and keep-awake. Modes: `plan`, `draft`, `full`, `check`, `revise`, `polish`.

**Stages** share one base class (`pipeline/stage.py`): `targets, check_inputs, build_prompt, schema, validate, advise, persist, adopt, finalize`. `adopt` recognises work saved just before a crash so it isn't generated twice.

| Stage | Name | Model profile | Output |
|---|---|---|---|
| A | spec | planner | BookSpec |
| B | design | planner | StoryDesign (acts, promises) |
| C | chapters | planner | Outline + ChapterPlans |
| D | cards | planner | SceneCards (planned at the prose model's natural scene length, D35) |
| E | drafts | prose | scene text, new version per draft |
| F | critique | critic/planner | findings, quality report, review-queue facts |
| G | revise | prose (+critic) | new unapproved draft versions, RevisionProposals, diffs |
| H | preview | none | watermarked Markdown (final export needs explicit approval) |

**ModelRouter** (`models/router.py`) is the only door to a model. Profiles `planner/prose/critic`; allow-list; three timeouts measured in *awake* time (laptop sleep must not trip them, D33); unload-before-switch (one resident model on 16 GB); cancellation; repetition guard. `generate_structured` enforces a JSON schema + allowed-ID enums + hard `validate` checks, then advisory `advise` checks with one repair attempt, then accepts with warnings or falls back to the best advisory-only value (D32).

**Drafting + memory** (Phase 4–5). `context/builder.py` renders, in fixed order, spec → core → codex → digests → chapter → recall → recap → card, trimming recall → digests → chapter → recap → codex when over budget. Memory is derived from each scene's *effective* version (approved, else active; D34): DIGEST (scene/chapter/act summaries keyed by a hash of their source), CORE (deterministic canon facts in force at that story time), RECALL (SQLite FTS5 over scene chunks). Drafts are cleaned of prompt echo, invented titles and meta lines (D36).

**Quality engine** (Phase 6, `docs/07`). Stage F per scene, on the effective text:
- `deterministic.py`: repetition, stock phrases (configurable list), leaked model text, POV, tense, length, missing participants.
- `extract.py`: the model proposes `(subject, predicate, value, quote)`. Code keeps a fact only if the quote is in the text (exact or ≥ 0.95 fuzzy, ≤ 40 words), the subject is a shown Codex ID, entity values resolve, literal values (age/date/colour/state) are visible in the quote, and the quote names the thing it is about (D37). State facts → review queue. Actors are placed at the scene card's location (derived, checked, never canon).
- `rules.py`: pure functions over observations, judged in **story order** from the Timeline, not narrative order: single-valued facts, death/temporal, knowledge, presence, object state, age/date, promises, travel; `dream`/`unreliable` scenes are relaxed.
- `critics.py`: craft rubric (8 dimensions, 1–5), per-character voice critic, adjudication of free-text conflicts ("sea-coloured" vs "blue"). Every returned quote is verified; quote-restating or empty notes are dropped.
- `check.py`: per-detector-family finding sync — dismissed/intentional/accepted findings are never reopened; findings that no longer occur become `fixed`. A `QualityReport` per scene version lets unchanged scenes be skipped and gives Stage G its baseline score.

Stage G (`stages/revise.py` + `quality/revision.py`) works on **accepted** findings only, pass by pass (continuity → voice → craft → slop). Per paragraph window: model rewrites the passage → splice → guards → keep or retry (≤ 2) → "needs author". An edit is kept only if: diff stays inside the window; ≥ 40 % of the passage's content words and every Codex name survive; evidence words changed in *words* (not punctuation); the raising detector no longer fires; no new high finding or canon contradiction; craft score didn't drop by more than ε = 1. A kept edit is its own unapproved draft version + `RevisionProposal` + diff. Revision never touches canon.

`lessons.py`: a finding type recurring in ≥ 3 of the last 10 checked scenes is *suggested* as a style rule; only the author adds it; active lessons are appended to the spec block of the drafting prompt.

## 6. CLI cheat sheet

```
run --mode plan|draft|full --idea "…" [--yes]     status · approve <gate|drafts> · resume · cancel · jobs
spec · design · outline · cards · draft [--scene --chapter --redraft --no-memory]
check [--scene --chapter --recheck --only deterministic|continuity|craft|voice]
findings [--accept id … --dismiss id … --intentional id …]   revise · polish --chapter ch02
facts list|accept|reject|retcon     lessons list|add|remove     codex · edit · diff · show · export [--preview]
memory status|update|rebuild|consolidate     backup · doctor · version
```

## 7. Status (honest)

- **Phases 0–6 done and committed:** foundations, storage, router/prompts, planning pipeline, drafting + Markdown export, long-book memory, quality engine (Stages F–G). 383 tests (375 fast, 8 need a live Ollama or are slow). Lint (ruff), types (pyright) and the fast tests run in a pre-commit hook.
- **Not started:** Phase 7 web app (FastAPI + React), 8 story map/timeline/Codex UI, 9 script mode (Fountain), 10 more exports (docx/epub/PDF) + hardening, 11 launcher. Roadmap with exit criteria: `docs/10`.
- **Deferred on purpose:** (a) embeddings/hybrid recall — lexical recall met its target on the fixture, revisit with real prose; (b) critic calibration set — needs ~50 author-labelled findings; (c) full real-model evaluations (see numbers below).

**Measured results:**
- Canon rules on the golden continuity book (`tests/golden/continuity_book`, 25 planted contradictions + 10 decoys), fed fixture observations: **25/25 found, 1/10 decoy false positives** (d09 needs model judgement).
- End to end (model extracts facts from prose → verified → rules), `scripts/eval_continuity.py`: llama3.2:1b 4/24; llama3.1 8B prompt v1 9/24; after prompt v2 + grounding/anchoring (D37) 8B reached **17/23 before the run was stopped** (decoys not reached). A full run was never completed — **do this first on the new machine.**
- Memory recall@context on a synthetic book: 25/25 (favourable fixture; not proof for real prose).
- Real full-agent trial (`scripts/try_agent.py fast`): PASSED in 40 min on the CPU laptop; 3 scenes (503/570/686 words), 22 findings, 2 edits kept, 3 refused.
- Real 8B prose sample: `examples/salt-letters` (2,204 words, 72 min, offline). Competent but generic prose. The 8B writes ~750–850 words per scene whatever length is requested (D35).

## 8. The new machine (Blackwell GPU) — what changes and what to do

Everything so far was tuned for an **i7-1165G7, 16 GB RAM, CPU only** laptop: 8B Q4 ≈ 3 tokens/s, 1B ≈ 10 tokens/s, `num_ctx = 6144`, `max_loaded_models = 1`, 15-minute-class timeouts, an 8-hour-style patience for long runs. Those numbers are **hardware limits, not design limits**. On a Blackwell GPU the same code should be one to two orders of magnitude faster and can use much larger models and contexts.

I don't know the exact GPU or VRAM on that machine — run `nvidia-smi` and decide model sizes from that.

**Keep:** strictly local, loopback-only, allow-list, author-decides, evidence-or-hypothesis, scoped revision. Faster hardware is a reason to run *more* checks, not to loosen verification.

**Change deliberately (all by config, no pipeline changes needed):**
1. **Install the local runtime with CUDA** (Ollama or a llama.cpp server). Keep it bound to `127.0.0.1`. `[runtime].adapter` supports `ollama`, `llamacpp`/`openai-local`, `fake`.
2. **Extend `allowed_models`** in `config/model-profiles.toml` with the bigger local models the owner chooses (keep the 8B/1B for regression comparison). Add each model's tokenizer family under `[runtime.tokenizer_family]` and build its offline tokenizer with `scripts/build_tokenizer.py` (the builder reads the GGUF; there is no `/api/tokenize` dependency).
3. **Retune profiles** per role: larger `num_ctx` (the context builder budgets from it automatically), `max_loaded_models` > 1 (or keep unload-before-switch), `min_version`, timeouts, and `prose.scene_words` — re-measure the model's natural scene length before changing it; D35 plans scenes at that size.
4. **Consider separate models per role** (e.g. a stronger critic than the writer). Profiles already allow a different model per `planner`/`prose`/`critic`; the router unloads/reloads as needed.
5. **Re-measure, don't assume.** Prompt templates (`facts.extract@2`, `revise.span@1`, critique prompts) were tuned against a 1B and an 8B. A stronger model may tolerate richer prompts and longer quote windows, or expose new failure modes.

## 9. What to do next (priority order)

1. **Run the full evals the CPU laptop couldn't afford.** `scripts/eval_continuity.py default` (and any new model profile) → recall/decoy numbers end to end; compare against the 17/23 partial. Save JSON reports to `docs/benchmarks/`. Exit target from Phase 6: seeded recall ≥ 80 %, decoy false positives ≤ 20 %.
2. **Run `scripts/try_agent.py default --keep`** (8B, full chain) and read the book. Then try a larger `--scenes` and a real multi-chapter novella (e.g. `tests/golden/novella_5ch`) to see long-book memory and Stage F/G on real prose. Check how often Stage G keeps vs. refuses edits and tune the guards (`MIN_KEPT`, `EPSILON`, attempts) from evidence.
3. **Build the critic calibration set** (Phase 6 leftover): author-label ~50 findings, measure per-critic precision per model profile; use it to set default severities for model-only findings.
4. **Evaluate embeddings/hybrid recall** now that GPU makes it cheap (`nomic-embed-text` is allow-listed but disabled; the `profiles.embedding` entry exists). Needs a real-prose evaluation, not the synthetic book.
5. **Phase 7a/7b — local web app** (FastAPI + React/Vite; bind `127.0.0.1` only; SSE events; share the Conductor with the CLI; optimistic concurrency). Exit criteria are in `docs/10`. Do 7a (authoring views) before 7b (Quality Desk, Fact Review).
6. Then Phase 8 (story map/timeline/Codex editor), 9 (script mode, Fountain), 10 (docx/epub/PDF + hardening), 11 (launcher).
7. Housekeeping: add a license if/when the owner decides one (the repo is public, but no license has been chosen — default is all rights reserved); refresh the README/`docs/README.md` status line when a phase lands.

## 10. Working conventions and traps (learned the hard way)

- **Commits:** the pre-commit hook runs `ruff format`, `ruff check --fix`, `pyright` and the fast tests via `uv run --no-sync …`. `uv` must be on PATH (see §3). A commit takes minutes — run it in the background. Line length 110. Co-author trailer is requested by the harness; follow whatever attribution instruction the session gives.
- **Tests:** `-m "not ollama and not slow"` is the fast set. `slow` = multi-process chaos tests. `ollama` = needs a live local Ollama. Use `.venv/Scripts/python -m pytest` (or `.venv/bin/python` on Linux) if `uv run` can't sync because an executable is in use.
- **Editing `src/` during a background run is unsafe** — the run imports live code. Wait or work in a worktree.
- **Don't write regexes or `\n` through shell heredocs/`sed`** — escapes get mangled (this broke files several times). Use the editor/Write tools, or put patch scripts in files.
- **Windows specifics** (irrelevant on Linux, but the code handles them): killing a process tree needs `taskkill /F /T` (the venv launcher is a separate process); `time.monotonic()` counts sleep, hence the awake clock; NTFS junction path-guard; CRLF/LF warnings are normal (`.gitattributes` forces LF).
- **Prompts are versioned.** Never edit a shipped template version in place; add `@N+1`, point the stage at it, and add a render context in `tests/unit/test_prompts.py` (that test fails for any template without one).
- **New stage checklist:** subclass `Stage`, register in `conductor.STAGES` and `MODES`, implement `adopt` for crash safety, emit events, add a fake-model integration test including cancel/resume (see `tests/integration/test_quality_loop.py`).
- **Small-model lessons that shaped the design:** small models loop (repetition guard, `min_length`), copy their prompt (echo stripping), invent IDs (ID enums), write ids where values belong (per-predicate value formats in `facts.extract@2`), and pass a quote check while carrying an invented value (value grounding, D37). Expect a *different* set of failure modes from a bigger model — measure first.
- **Decision log is the memory of the project.** Record non-obvious choices as `D<n>` in `docs/12` (currently D1–D37) and architecture-level ones as ADRs in `docs/adr/`.

## 11. Glossary

- **Canon** — author-accepted story facts (`canon/facts.jsonl`), each with `valid_from`/`valid_to` scenes resolved through the Timeline's story order.
- **Review queue** — candidate facts extracted from prose awaiting accept/reject.
- **Finding** — an evidence-linked quality issue; lifecycle `open → accepted → fixed`, or `dismissed` / `intentional`; "needs author" = accepted but Stage G could not safely fix it.
- **Effective version** — a scene's approved draft, else its active draft. Memory and Stage F read this.
- **Story order vs narrative order** — flashbacks are checked at their place in story time (Timeline `story_index`), not where they appear in the book.
- **Gate** — a pause for author approval (spec, design, outline, scene_cards). Drafts are approved separately and never by `--yes`.
- **Profile** — a named model configuration (planner/prose/critic) in `config/model-profiles*.toml`.
