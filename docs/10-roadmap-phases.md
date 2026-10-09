# 10 — Roadmap: Phases

Twelve phases (0–11), grouped into five milestones. Each phase has a goal, a task checklist, deliverables, exit criteria (the phase isn't done until these pass), dependencies and risks. Effort is a rough solo-developer estimate with AI-assisted coding; Phase 0's benchmark will sharpen it.

| Milestone | Phases | What you can do at the end | Est. |
|---|---|---|---|
| **M1 — MVP (CLI)** | 0–4 | Idea → outlined, drafted book → Markdown, fully offline, resumable | ~7–9 wks |
| **M2 — Beta engine** | 5–6 | Long books stay consistent; findings with evidence; revision diffs | ~5–6 wks |
| **M3 — Workbench** | 7–8 | Everything in a local web app, incl. story map, timeline, Codex | ~5–7 wks |
| **M4 — Story + Script** | 9 | Screenplays/TV episodes end to end in Fountain/PDF | ~3 wks |
| **M5 — v1.0** | 10–11 | DOCX/EPUB, hardened, one-command launch on your machine | ~2.5–3 wks |

```
Ph0 ─▶ Ph1 ─▶ Ph2 ─▶ Ph3 ─▶ Ph4 ═▶ M1
                              ├─▶ Ph5 ─▶ Ph6 ═▶ M2 ──┐
                              └─▶ Ph7a (API + authoring views) ──┴─▶ Ph7b (quality views) ─▶ Ph8 ═▶ M3
                    Ph6 + Ph7b ─▶ Ph9 ═▶ M4
                    all       ─▶ Ph10 ─▶ Ph11 ═▶ v1.0
```

---

## Phase 0 — Foundations & model spike  (≈1 wk)

**Goal:** a working repo and hard numbers on what local models can do on your machine.

- [x] Create repo per [03 § layout](03-architecture.md#repository-layout): single `uv` project ([ADR-0001](adr/0001-single-package-layout.md)), ruff, pyright, pytest, pre-commit
- [x] Move `plan/` → `docs/` (build guide + diagrams → `docs/reference/`); add `CONTRIBUTING.md`, `CHANGELOG.md`, ADR template
- [x] ~~Pick license~~ (personal use, D17: none needed)
- [x] Install `uv` (0.12.24, user install); `git init` (local only, no remote, per Q10); local pre-commit hooks (ruff, pyright, pytest-quick)
- [x] Minimal Ollama smoke script: `num_ctx`, JSON-schema `format`, `done_reason=length` confirmed on both models (2026-10-09, `scripts/smoke_ollama.py`)
- [x] Extend smoke script: streaming (TTFT 0.9 s on 1B), cancellation (dropping the stream stops generation, next request not queued), model switch with explicit unload (1B→8B→1B clean, no HTTP 500)
- [x] ~~Benchmark 3–4 candidate models~~. Models fixed by D16: `llama3.1:8b-instruct-q4_K_M` (default), `llama3.2:1b` (fast tests)
- [x] Measure chars-per-token (3.7 JSON / 4.5 prose, Llama-3 tokenizer)
- [x] Llama-3 tokenizer built **offline from the local GGUF** (`scripts/build_tokenizer.py`, no download); exact match with Ollama counts (+1 BOS) on 8/8 strings; identical for both D16 models
- [ ] Write the **mini-book benchmark** fixture (spec + 3 chapters + 6 cards + 2 scenes) and `scripts/bench_models.py`. Run on 1B (minutes) and 8B (≈ 1–2 h, overnight-friendly); record schema-valid %, tok/s, prefix-cache hit rate, your quality rating
- [x] **Golden test book** outline v0: `tests/golden/continuity_book/outline.yaml`, 30 scenes, 25 contradictions across all 8 rule types, 10 decoys (flashback, dream, unreliable, on-page transfers); structure checked by `tests/unit/test_golden_fixtures.py`

**Deliverables:** repo skeleton, pre-commit green, `docs/benchmarks/2026-10-09-smoke.md` (done) + mini-book benchmark, golden fixtures v0.
**Exit criteria:** `uv run pytest` passes on Windows; mini-book benchmark completes on both models; D16 confirmed or revised from the benchmark.
**Risks:** 8B too slow for comfortable iteration → mitigation: 1B profile set for wiring, Fake adapter for tests, small context, prefix caching; structured JSON looked solid in the smoke test, but long/nested schemas are untested, so keep them small and flat.

---

## Phase 1 — Domain contracts & workspace storage  (≈1.5 wks)

**Goal:** the data layer everything else stands on.

- [ ] Pydantic models for every entity in [04](04-data-model.md), with `schema` version fields
- [ ] Form profiles (`novel`, `short-story`; stubs for `screenplay`, `tv-episode`, `stage-play`)
- [ ] ID minting service (stable, prefixed, collision-safe)
- [ ] Workspace store: create project, read/write JSON & Markdown, versioned artifacts with active/approved pointers, input hashing, staleness detection
- [ ] Atomic writes (`tmp → fsync → os.replace`) + Windows-safe file locking
- [ ] Path guard: project-root confinement; reject traversal, absolute paths, symlinks/junctions
- [ ] Append-only audit log (JSONL)
- [ ] SQLite index (SQLModel) + `reindex` that rebuilds from files
- [ ] Backup/restore (zip) of a project
- [ ] CLI: `init`, `reindex`, `backup`, `restore`, `doctor` (storage part)
- [ ] Unit + property tests (hypothesis) for versioning, pointers, path guard, reindex round-trip

**Deliverables:** `packages/domain`, `packages/storage`, `packages/policy`, CLI basics.
**Exit criteria:** delete `index.sqlite` → `reindex` reproduces identical query results; path-escape test suite passes on Windows; 100% of entity models round-trip JSON.
**Risks:** over-modelling early → keep fields optional where unsure; migrations are cheap because files carry schema versions.

---

## Phase 2 — Model router & prompt registry  (≈1 wk)

**Goal:** one reliable way to call a local model and get valid structured output.

- [ ] `ModelRouter` interface; Ollama adapter; llama.cpp adapter; local-only OpenAI-compatible adapter; Fake adapter (record/replay)
- [ ] `generate_structured` with schema-constrained decoding, Pydantic validation, repair retry, temperature backoff
- [ ] Allowed-ID validation helper for model outputs
- [ ] Token counting chain (llama.cpp tokenize → cached HF tokenizer → calibrated estimate)
- [ ] Profiles loaded from `config/model-profiles.toml`; health checks; timeouts; cancellation
- [ ] Prompt registry: Jinja2 templates with frontmatter, versioning, project overrides, render hash
- [ ] Skills loader (`SKILL.md`), first skills: three-act, save-the-cat, hero's journey
- [ ] Run recorder: `runs/<job>/prompt.txt, raw_output.txt, meta.json`
- [ ] CLI: `doctor` (runtime part), `bench`

**Deliverables:** `packages/models`, `packages/prompts`.
**Exit criteria:** ≥ 95% first-try schema-valid on the benchmark's planning calls with the default profile, 100% after retries; cancellation leaves no partial versions; Fake adapter lets the whole test suite run without a model.
**Risks:** runtime API differences between Ollama versions → pin a tested minimum version, `doctor` warns.

---

## Phase 3 — Planning pipeline (Stages A–D) + Conductor  (≈2 wks)

**Goal:** idea → approved spec, design, outline and scene cards, resumable.

- [ ] Stage protocol + base class (check_inputs / build_prompt / generate / validate / persist)
- [ ] Conductor: job table, state machine (`queued → running → awaiting_approval → succeeded/failed/cancelled`), retries, checkpoint after every stage, resume from last valid checkpoint
- [ ] Stage A (Book Spec) + enhance, preserving `original`, diff output
- [ ] Stage B (Story Design) + plot promises + Codex character candidates
- [ ] Stage C (Chapter Plan) generated per act + whole-outline enhance + deterministic outline checks
- [ ] Stage D (Scene Cards) per chapter + card checks
- [ ] Approval gates (configurable per stage) in CLI. `--yes` answers planning gates A–D only, audited as `via: "--yes"`. It never accepts Codex/canon candidates. See [05 § Approval & `--yes`](05-pipeline.md#approval--yes)
- [ ] Per-chapter ChapterPlan files + declared-input staleness ([04 § Staleness](04-data-model.md#staleness-what-each-artifact-depends-on))
- [ ] Timeline file: append linear scenes automatically; validated placement for non-linear scenes
- [ ] Manual edit workflow: `inkforge edit <artifact>` opens in `$EDITOR`, saves as user version, marks dependants stale
- [ ] Modes: `run --mode plan`, `--until <stage>`
- [ ] Integration tests with Fake adapter; one real-model smoke test (nightly/manual)

**Deliverables:** `packages/pipeline` with stages A–D; CLI planning commands.
**Exit criteria:** kill the process at a random point during `run --mode plan` 20 times → every resume completes with valid artifacts; each stage runs alone from saved inputs; editing chapter N marks only chapter N's cards stale (neighbours flagged "review suggested" only if `purpose`/`outcome` changed); reordering chapters marks nothing stale.
**Risks:** outline quality on small models → per-act generation, enhance passes, structure skills; author edits are first-class.

---

## Phase 4 — Drafting (Stage E) + basic context + Markdown export  ★ M1 MVP  (≈2 wks)

**Goal:** the first end-to-end vertical slice, offline: spec → outline → cards → drafted scenes → one continuity check → Markdown.

- [ ] Context builder v1: spec essentials, scene card, Codex for present characters, previous-scene recap, and a **chapter stand-in** in place of digests (chapter plan `purpose`/`conflict`/`outcome` + `concrete_event` one-liners of earlier cards in the chapter; no model summaries until Phase 5); token budgeting; **context manifest**
- [ ] Stage E: scene drafting with voice cards, length targeting
- [ ] Truncation detection (`finish_reason`, mid-sentence) + safe continuation with overlap stripping
- [ ] Draft checks (participants present, meta leakage, length, unknown names → candidates)
- [ ] Version history + word-level diff utility (`inkforge diff`)
- [ ] One deterministic continuity check end to end (name/alias consistency) producing a Finding with evidence
- [ ] Approve versions (`--scene/--chapter/--all`, bulk lists + confirms); Stage H-lite: Markdown export of approved scenes in outline order + preflight (missing/unapproved/stale)
- [ ] `export --preview`: watermarked draft export of active versions (unapproved allowed, listed in preflight)
- [ ] `run --mode full [--yes]` for a short story end to end (ends at draft preview; final export needs explicit approve)
- [ ] **Offline test #1:** network disabled, full short-story run + export

**Deliverables:** MVP CLI; sample project in `examples/`.
**Exit criteria:** a 5-chapter novella fixture runs end to end offline (on `llama3.2:1b` for the wiring check; one short-story run on the 8B, overnight is fine); export order matches outline; no unapproved version in export; manifests recorded for every draft.
**Risks:** prose quality → measure early with the benchmark; separate prose profile with higher temperature; lessons/skills later.

---

## Phase 5 — Long-book memory  (≈2 wks)

**Goal:** books of 60–90k words without forgetting.

- [ ] Scene / chapter / act digests generated on approval
- [ ] CORE builder (deterministic from canon facts + character state + promises, hard budget)
- [ ] FTS5 indexing of approved scenes (paragraph chunks with offsets) and Codex
- [ ] Deterministic retrieval queries from scene cards; ranking; de-dup
- [ ] Context builder v2 with the full priority order and trimming algorithm from [06](06-memory-and-context.md)
- [ ] Memory update triggers (approve, edit, card change)
- [ ] `memory rebuild` and `memory consolidate`
- [ ] Optional: embeddings + FAISS hybrid retrieval behind a flag
- [ ] Context recall evaluation on the golden book

**Deliverables:** `packages/memory`, context builder v2.
**Exit criteria:** recall@context ≥ 90% on golden dependent facts; context test (overlong input trimmed by priority, manifest correct) passes; rebuild reproduces memory from files.
**Risks:** summaries drift from text → digests are regenerated, never edited incrementally; manuscript remains truth.

---

## Phase 6 — Quality engine (Stages F–G)  ★ M2 Beta engine  (≈3 wks)

**Goal:** evidence-linked critique and safe, scoped revision.

- [ ] Deterministic checks suite (names, repetition, slop lint, meta, POV/tense, length)
- [ ] Fact extraction with verbatim-quote verification → review queue; CLI review
- [ ] Canon store with temporal validity resolved through the Timeline (`story_index`); predicate vocabulary incl. item predicates (`held_by`, `located_at`, `condition`)
- [ ] Conflict rules: single-valued, temporal, knowledge, presence, object state, age/date, promises (travel opt-in)
- [ ] Scene `temporal_mode` relaxations per [07](07-quality-engine.md#3-canon-conflict-rules) (flashback/flashforward/dream/unreliable)
- [ ] Model adjudication for ambiguous conflicts
- [ ] Voice critics per character; craft rubric
- [ ] Finding lifecycle + severities + "intentional" suppression
- [ ] Stage G: scoped revision instructions, span edits, diff, re-check, keep-only-if-better, attempt limits
- [ ] Specialist pass order for "polish chapter"
- [ ] Lessons (promote recurring finding → style rule)
- [ ] Golden continuity evaluation + critic calibration set

**Deliverables:** `packages/quality`; `check`, `revise`, `facts review` commands.
**Exit criteria:** seeded contradiction recall ≥ 80%, decoy false-positive ≤ 20%; every finding has verifiable evidence; no canon change without confirmation (test); revision never changes text outside allowed spans for span edits.
**Risks:** model critics noisy → advisory only, evidence verification, calibration set, severity defaults low for model-only findings.

---

## Phase 7 — Local web app: API + core views  (≈3–4 wks total, in two parts)

**Goal:** everything the CLI does, in a browser on localhost.

Phase 7 is split because the quality views have nothing to show until Phase 6 exists.

### 7a — API + authoring views  (≈2–3 wks; needs Phase 4 only)

- [ ] FastAPI app: REST resources, action endpoints, SSE events, OpenAPI → generated TS client
- [ ] Job runner inside API; CLI and API share the same pipeline
- [ ] Optimistic concurrency (`If-Match` / 409)
- [ ] React + Vite + Tailwind/shadcn scaffold; routing; TanStack Query
- [ ] Views: Projects, Spec & Design, Outline (tree + drag reorder), Manuscript (Tiptap, versions, diff, approve, word count), Run Console, Settings
- [ ] Manuscript shows Phase 4's name/alias findings inline (read-only list), so the finding plumbing is exercised early
- [ ] Playwright e2e on the Fake adapter
- [ ] Bind `127.0.0.1` only; CORS locked to the local origin

**Exit criteria (7a):** a novella can be planned, drafted, approved and exported (final + preview) purely from the UI; e2e green; killing the API mid-job and restarting resumes from checkpoint with the UI showing correct state.

### 7b — Quality views  (≈1 wk; needs Phase 6 + 7a)

- [ ] Quality Desk: findings table, side-by-side evidence, accept/dismiss/intentional, launch revision, review diffs (keep-if-better scores shown)
- [ ] Fact Review: review queue with source quotes, bulk accept; Codex candidate queue (characters, items, locations)
- [ ] Promise registry panel (open/paid/abandoned) — read + status changes via registry
- [ ] e2e: check → accept finding → revise → accept diff → approve, all from the UI

**Exit criteria (7b):** a novella can be checked and revised purely from the UI; no canon change without a confirm click (e2e-tested).

**Deliverables:** `apps/api`, `apps/web`.
**Risks:** UI scope creep → views above only; story map waits for Phase 8.

---

## Phase 8 — Story map, timeline & Codex UI  ★ M3 Workbench  (≈2–3 wks)

**Goal:** OpenWrite-style visual planning backed by the typed data.

- [ ] React Flow story map with custom nodes, filters, ELK auto-layout, zoom clusters
- [ ] Progressive expansion: "expand node" runs the next planning stage for that node only
- [ ] Promote chapter/scene node → manuscript (create cards / start draft)
- [ ] Graph edits = validated outline edits, journaled, undo/redo
- [ ] Setup/payoff edges and open-promise highlighting
- [ ] Timeline view: story-time events, character presence lanes, conflict flags
- [ ] Codex pages: characters (voice card editor, relationships, appearances, facts), locations, lore; cross-links
- [ ] Keyboard-accessible equivalents

**Deliverables:** Story Map, Timeline, Codex views.
**Exit criteria:** every graph action round-trips to files and back (reload shows same state); reorder validation prevents breaking causal links without confirmation; usability pass on a real project.

---

## Phase 9 — Script mode  ★ M4 Story + Script  (≈3 wks)

**Goal:** screenplays and TV episodes through the same pipeline.

- [ ] Form profiles: `screenplay`, `tv-episode` (and `stage-play` stretch)
- [ ] Script templates for A–E: logline/format spec, beat sheet skills (Save the Cat, 8-sequence, TV act structure), sequences, sluglines, Fountain drafting
- [ ] Fountain parser + linter (sluglines, cues, parentheticals, transitions, dual dialogue)
- [ ] Script-specific checks: page timing (~1 page/min), dialogue/action ratio, camera-direction overuse, character cue consistency with Codex
- [ ] CodeMirror Fountain editor mode + live page count
- [ ] Exporters: `.fountain`, screenplay PDF (industry layout), FDX (stretch)
- [ ] Golden screenplay fixture (short film, ~15 pages)
- [ ] Stretch: "adapt" stage — novel chapter → script scenes

**Deliverables:** script mode in CLI + UI.
**Exit criteria:** a 15-page short film runs end to end offline; output parses with zero Fountain errors; PDF matches standard layout on a visual check.

---

## Phase 10 — Export & hardening (Stage H full)  (≈2 wks)

**Goal:** release-quality outputs and guarantees.

- [ ] DOCX exporter (manuscript format: title page, chapter headings, scene breaks, styles)
- [ ] EPUB3 exporter (ebooklib) + validation (epubcheck run at setup time if Java available, otherwise structural checks)
- [ ] Full preflight report (findings, promises, stale, duplicates, unsupported features)
- [ ] Backups before bulk ops; restore tests across machines
- [ ] Data migrations tests (old schema fixtures → current)
- [ ] Security tests: path escape, loopback binding, no outbound requests (socket monitor), no remote endpoints in config
- [ ] **Full offline acceptance suite** ([11](11-testing-and-acceptance.md))
- [ ] Performance pass: long-book (80k words) run time and memory profile

**Exit criteria:** all acceptance tests in [11](11-testing-and-acceptance.md) pass on Windows; exports open cleanly in Word/LibreOffice and an EPUB reader.

---

## Phase 11 — Launcher & daily-use polish  ★ M5 v1.0  (≈3–5 days)

**Goal:** comfortable daily use on *your* machine (personal tool, D17; no installer or distribution).

- [ ] One-command launcher (`inkforge up`: starts API + serves built UI + opens browser; warms the default model)
- [ ] `inkforge doctor` final: Ollama reachable, both D16 models present, free RAM, workspace writable, offline config clean
- [ ] Windows convenience: a `.cmd`/shortcut that runs `uv run inkforge up`
- [ ] Short personal notes in `docs/`: daily workflow, overnight-run recipe, backup/restore, troubleshooting (OOM on model load, slow first prompt)
- [ ] Version tag + changelog

**Exit criteria:** from a fresh reboot, `inkforge up` → open a project → draft a scene → export, without consulting code.

---

## After v1.0 (backlog)

Reader panels (synthetic personas), title/blurb tournaments, series bible across projects, prose→script adaptation, LoRA style tuning on your own text (research track; fits your interpretability/model-editing work), larger models if the hardware changes. Cloud providers and collaboration are permanently out (D17, D18).

## Working rhythm per phase

1. Open an ADR for any non-obvious choice before coding it.
2. Write/extend golden fixtures *before* the feature.
3. Build against the Fake adapter; run one quick real-model smoke on `llama3.2:1b`, then one on `llama3.1:8b-instruct-q4_K_M` at the end (overnight if long).
4. Close the phase only when exit criteria pass; update `CHANGELOG.md` and the status line in `README.md`.
