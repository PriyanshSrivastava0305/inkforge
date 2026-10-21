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
- [x] **Mini-book benchmark** (`scripts/bench_models.py`, `tests/golden/mini_book/`): 1B 5.5 min, 8B 23.3 min, 6/6 structured calls valid first try on both; findings in [ADR-0002](adr/0002-default-model-profile.md). *Your quality rating of the drafts is still open* (see the two benchmark reports)
- [x] **Golden test book** outline v0: `tests/golden/continuity_book/outline.yaml`, 30 scenes, 25 contradictions across all 8 rule types, 10 decoys (flashback, dream, unreliable, on-page transfers); structure checked by `tests/unit/test_golden_fixtures.py`

**Deliverables:** repo skeleton, pre-commit green, `docs/benchmarks/2026-10-09-smoke.md` (done) + mini-book benchmark, golden fixtures v0.
**Exit criteria:** `uv run pytest` passes on Windows; mini-book benchmark completes on both models; D16 confirmed or revised from the benchmark. **Status: done 2026-10-09** (D16 confirmed by ADR-0002).
**Risks:** 8B too slow for comfortable iteration → mitigation: 1B profile set for wiring, Fake adapter for tests, small context, prefix caching; structured JSON looked solid in the smoke test, but long/nested schemas are untested, so keep them small and flat.

---

## Phase 1 — Domain contracts & workspace storage  (≈1.5 wks)

**Goal:** the data layer everything else stands on.

- [x] Pydantic models for every entity in [04](04-data-model.md), with `schema` version fields (`src/inkforge/domain/models.py`)
- [x] Form profiles (`novel`, `short-story`; stubs for `screenplay`, `tv-episode`, `stage-play`, marked `available = false`)
- [x] ID minting service (stable, prefixed, collision-safe; gaps never reused; counters persisted in `project.json`)
- [x] Workspace store: create project, read/write JSON & Markdown, versioned artifacts with active/approved pointers, input hashing, staleness (hard = stale, neighbour context = review)
- [x] Atomic writes (`tmp → fsync → os.replace`, retried on transient Windows `PermissionError`) + OS file lock per project (released if the process dies)
- [x] Path guard: project-root confinement; rejects traversal, absolute/drive/UNC paths, reserved device names, ADS, trailing dots, symlinks **and NTFS junctions** (tested)
- [x] Append-only audit log (JSONL, `O_APPEND` + fsync)
- [x] SQLite index (stdlib `sqlite3`, [ADR-0003](adr/0003-sqlite-index-stdlib.md)) + `reindex`; incremental refresh proven equal to a rebuild
- [x] Backup/restore (zip + hash manifest; zip-slip and tamper rejected; staged restore, never overwrites)
- [x] CLI: `init`, `projects`, `reindex`, `backup`, `restore`, `doctor` (storage part); UTF-8 console output on Windows
- [x] Unit + property tests (hypothesis) for versioning, pointers, path guard, reindex round-trip (153 tests)

**Deliverables:** `inkforge.domain`, `inkforge.storage`, `inkforge.policy`, CLI basics. **Status: done 2026-10-09.**
**Exit criteria:** delete `index.sqlite` → `reindex` reproduces identical query results; path-escape test suite passes on Windows; 100% of entity models round-trip JSON.
**Risks:** over-modelling early → keep fields optional where unsure; migrations are cheap because files carry schema versions.

---

## Phase 2 — Model router & prompt registry  (≈1 wk)

**Goal:** one reliable way to call a local model and get valid structured output.

- [x] `ModelRouter`; Ollama adapter; local OpenAI-compatible adapter (llama.cpp mode adds `/tokenize`); Fake adapter (scripted, record/replay, auto-from-schema, simulated RAM limit)
- [x] `generate_structured`: constrained decoding, allowed-ID check, Pydantic validation, stage validators, repair retry, temperature backoff, truncation-aware token growth
- [x] **Repetition guard** (not in the original plan): stops degenerate loops mid-stream and repairs. The 1B looped twice in testing, 2+ min wasted each time on CPU
- [x] Allowed-ID validation helper (`models/allowed.py`) plus **ID enums in schemas** so constrained decoding can't invent references
- [x] Token counting chain (runtime tokenize → offline-built cached tokenizer → calibrated estimate)
- [x] Profiles from `config/model-profiles.toml`; three timeouts (first token / idle / total); cancellation; unload-before-switch; `min_version`
- [x] Prompt registry: sandboxed Jinja2 + StrictUndefined, frontmatter, versioning, project overrides, render hash
- [x] Skills loader (`SKILL.md`): three-act, save-the-cat, heros-journey
- [x] Run recorder: `runs/<job>/prompt.txt, raw_output.txt, meta.json, partial.txt`
- [x] CLI: `doctor` (runtime part), `bench`, `prompts`

**Deliverables:** `inkforge.models`, `inkforge.prompts`. **Status: done 2026-10-09.**
**Exit criteria:** ≥ 95% first-try schema-valid on the benchmark's planning calls with the default profile, 100% after retries; cancellation leaves no partial versions; Fake adapter lets the whole test suite run without a model.
**Result:** router benchmark 6/6 first try on both 8B (22.9 min) and 1B (5.8 min) ([reports](benchmarks/)); cancelled text only ever reaches `partial.txt` (tested at router and conductor level); 220 tests run without a model.
**Risks:** runtime API differences between Ollama versions → pin a tested minimum version, `doctor` warns.

---

## Phase 3 — Planning pipeline (Stages A–D) + Conductor  (≈2 wks)

**Goal:** idea → approved spec, design, outline and scene cards, resumable.

- [x] Stage base class (check_inputs / build_prompt / schema / validate / persist / adopt / finalize) — `pipeline/stage.py`
- [x] Conductor: jobs with per-step checkpoints, gates, resume, cancel file, one retry for transient runtime errors, `.run.lock`, crash `repair()` at job start
- [x] Stage A (Book Spec) + enhance with field diff; author-set fields re-applied by code on every version
- [x] Stage B (Story Design) + plot promises in the registry + character candidates (reused by name, never duplicated)
- [x] Stage C (chapter plans) per act + optional whole-outline enhance + deterministic outline checks (warnings)
- [x] Stage D (scene cards) per chapter: ID enums, location resolution/candidates, value-charge and non-linear placement checks
- [x] Approval gates; `--yes` answers planning gates only, audited `via: "--yes"`; Codex candidates need `inkforge codex --accept`
- [x] Per-chapter ChapterPlan files + declared-input staleness; chapter/scene IDs reused on regeneration
- [x] Timeline: linear scenes follow the previous linear scene; flashbacks/flashforwards must name `happens_after`
- [x] Manual edit workflow: `inkforge edit …` via `$EDITOR` (quoted Windows paths handled), user version, staleness report
- [x] Modes: `run --mode plan [--until stage] [--yes]`; standalone `spec/design/outline/cards`; `jobs/resume/cancel/approve/status/codex`
- [x] Integration tests on the Fake adapter + real-model smoke run (`llama3.2:1b`)

**Deliverables:** `inkforge.pipeline` with stages A–D; CLI planning commands. **Status: done 2026-10-09.**
**Exit criteria:** kill the process at a random point during `run --mode plan` 20 times → every resume completes with valid artifacts; each stage runs alone from saved inputs; editing chapter N marks only chapter N's cards stale (neighbours flagged "review suggested" only if `purpose`/`outcome` changed); reordering chapters marks nothing stale.
**Result:** chaos test (`tests/integration/test_chaos_resume.py`): 20 hard kills (whole process tree) per run, each verified by re-acquiring the run lock; seeds 5700, 1234, 777 → 20/20 kills interrupted a running job, 20 resumes, zero integrity problems, index identical after rebuild. *Correction (2026-10-10):* the first version of this test used `Popen.kill()`, which on Windows only kills the venv launcher, so its earlier "20/20/30 kills" results overstated what was proven; fixed in commit 6307dff.
**Risks:** outline quality on small models → per-act generation, enhance passes, structure skills; author edits are first-class.

---

## Phase 4 — Drafting (Stage E) + basic context + Markdown export  ★ M1 MVP  (≈2 wks)

**Goal:** the first end-to-end vertical slice, offline: spec → outline → cards → drafted scenes → one continuity check → Markdown.

- [x] Context builder v1 (`context/builder.py`): spec, Codex + voice cards for present characters, chapter stand-in, previous-scene recap (~400 tokens), card; stable-first render order, priority trimming (stand-in → recap → codex; card never), `ContextTooSmall` instead of silent loss; **context manifest** per draft with per-section tokens, disposition and prefix hashes
- [x] Stage E (`stages/draft.py`, prompt `draft.scene@2`): default targets = scenes without a draft or with a stale one; `--scene/--chapter/--redraft`
- [x] Truncation (`length`, `repetition`, mid-sentence) → up to 3 continuations (`draft.continue@2`) with overlap stripping; hard cap 160% of target
- [x] Meta-line cleanup at the edges only ("Here is the scene:", headings, labels, notes, rules); prose that merely starts with "Chapter" is kept
- [x] Draft checks: length (±35%), participants named, meta leakage, first-person POV, unknown recurring names (reported, not auto-added), still-truncated
- [x] Word-level diff (`inkforge diff`), `inkforge show` (any version, or a draft's manifest)
- [x] Continuity rule: name/alias typos (Damerau distance, catches transpositions) → Findings with verbatim evidence spans; replaced on redraft; `inkforge findings [--dismiss|--intentional]`
- [x] `approve drafts --scene/--chapter/--all [--version]` lists + confirms; Markdown export of approved versions in outline order + preflight (missing/unapproved/stale/empty chapters/high findings/newer-than-approved)
- [x] `export --preview`: watermarked, active versions, unapproved listed per chapter
- [x] `run --mode full [--yes]` = plan → drafts → preview stage; `--mode draft`; short stories export without chapter headings
- [x] **Offline test #1**: `tests/integration/test_offline_acceptance.py` runs the real CLI with a socket guard (`tests/support/offline/sitecustomize.py`) that fails and logs any non-loopback connection or DNS lookup

**Deliverables:** MVP CLI; sample project in `examples/`. **Status: done 2026-10-10** (see result).
**Exit criteria:** a 5-chapter novella fixture runs end to end offline (on `llama3.2:1b` for the wiring check; one short-story run on the 8B, overnight is fine); export order matches outline; no unapproved version in export; manifests recorded for every draft.
**Result (1B, offline):** `test_offline_acceptance.py` passed — the 5-chapter novella through the real CLI with the network guard: plan → 10 drafts → approve → final export, 34 min, 9,571 words, **zero non-loopback connection attempts**, export order = outline, a manifest for every draft, integrity clean. Real continuation used once (a scene hit max_tokens); two hard repairs succeeded (broken JSON, missing protagonist).
**Real-run findings fixed before sign-off:** (1) the first real run *failed* because the 1B kept writing two chapters with the same purpose after repairs → craft rules became advisory (D32); (2) 150 false "Jame vs James" name findings from an `rstrip("'s")` possessive bug → 0 after the fix; (3) the 1B scattered bare timestamp lines through its prose → 29 removed by cleanup; (4) Codex names are trimmed.
**8B short story (offline):** first attempt (2026-10-10) died when the laptop slept and hibernated on battery mid-call → sleep-safe timeouts + keep-awake (D33). Rerun 2026-10-12 on AC **passed**: 72 min, 11 model calls all valid first try, 2,204 words, zero network attempts. Sample in [`examples/salt-letters/`](../examples/salt-letters/README.md). Follow-ups from reading it: scenes ended at 55–70% of target → one extension pass under 60%; first-person narrator exempt from the participants check; contractions ("I'd") no longer read as names.
**Risks:** prose quality → measure early with the benchmark; separate prose profile with higher temperature; lessons/skills later.

---

## Phase 5 — Long-book memory  (≈2 wks)

**Goal:** books of 60–90k words without forgetting.

- [x] Scene / chapter / act digests (`memory/digests.py`): hash-keyed, so staleness propagates upward by itself; model-written with a deterministic extract fallback. Built right after each scene is drafted, from the **effective version** (D34), not only on approval
- [x] CORE builder (`memory/core.py`): deterministic; world rules, canon facts valid at the scene's *story time* (a flashback sees the past), open promises, time anchor; hard 800-token budget, lowest priority dropped first; candidates never included
- [x] RECALL (`memory/recall.py`): paragraph chunks (~400–1,200 chars) with exact offsets in `recall.sqlite` (FTS5/BM25); incremental `sync()`, self-healing if the file is corrupt
- [x] Deterministic retrieval queries from the scene card (names, aliases, location, items, promise keywords); only scenes earlier in narrative order; de-duplicated against the recap
- [x] Context builder v2 (prompt `draft.scene@3`): render order spec → CORE → codex → digests → chapter plan → RECALL → recap → card; trimming ladder RECALL (lowest first) → digests (acts → earlier chapters → this chapter) → chapter plan → recap → codex; card and CORE never trimmed
- [x] Memory follows the text automatically (hash-keyed): approve, edit, redraft and card changes need no explicit triggers
- [x] `inkforge memory status|update|rebuild|consolidate [--extract]`; `draft --no-memory`, `--extract-digests`
- [ ] Optional: embeddings + FAISS hybrid retrieval — **deferred**: lexical recall met the target on the fixture; revisit with real-prose evaluation in Phase 6
- [x] Context recall evaluation on the golden memory book (`tests/support/memory_book.py`, `tests/integration/test_recall_eval.py`)

**Deliverables:** `inkforge.memory`, context builder v2. **Status: done 2026-10-12.**
**Exit criteria:** recall@context ≥ 90% on golden dependent facts; context test (overlong input trimmed by priority, manifest correct) passes; rebuild reproduces memory from files.
**Result:** recall@context **25/25 (100%)** on the golden memory book (30 scenes, 11.8k words, 25 planted facts needed 3–13 scenes later) at a mean prompt of 2,755 tokens (budget 3,993). Baselines: "last three scenes" 3/25 (12%); memory off ≤ 2/25; "all earlier text" would need ~7,700 tokens. Rebuild reproduces digests and the recall index exactly. **Caveat:** the fixture is synthetic and favourable (distinctive entity names, cards that name the entity); it proves the mechanism, not real-prose recall. Phase 6's continuity book is the harder test.
**Real-model checks (2026-10-13):** *1B* with memory on first copied its prompt into the scene (427 of 1,323 words, incl. recalled passages) and polluted the digests → echo stripping, `draft.scene@4`, near-copy check (D36); rerun (resumed from a checkpoint after an interruption) clean: no residual echo, distinct digests, zero network attempts. *8B* scene with memory: no echo, accurate model digest with useful "changes", context 2,479/3,993 tokens, ~13 min per scene of which ~3.5 min are the three digest calls. Scene length stays ~750–850 words whatever is requested → scenes are now planned at that size (D35).
**Risks:** summaries drift from text → digests are regenerated, never edited incrementally; manuscript remains truth.

---

## Phase 6 — Quality engine (Stages F–G)  ★ M2 Beta engine  (≈3 wks)

**Goal:** evidence-linked critique and safe, scoped revision.

- [x] Deterministic checks suite (names, repetition, slop lint, meta, POV/tense, length, participants) — `quality/deterministic.py`
- [x] Fact extraction with verbatim-quote verification → review queue; CLI review (`inkforge facts list|accept|reject|retcon`)
- [x] Canon store with temporal validity resolved through the Timeline (`story_index`); predicate vocabulary incl. item predicates (`held_by`, `located_at`, `condition`)
- [x] Conflict rules: single-valued, temporal, knowledge, presence, object state, age/date, promises (travel opt-in) — `quality/rules.py`, no model; input is observations in a controlled vocabulary
- [x] Scene `temporal_mode` relaxations per [07](07-quality-engine.md#3-canon-conflict-rules) (flashback/flashforward/dream/unreliable)
- [x] Model adjudication for ambiguous conflicts (free-text single-valued: no → dropped, unclear → hypothesis)
- [x] Voice critics per character; craft rubric (quotes verified; weak or quote-restating notes dropped)
- [x] Finding lifecycle + severities + "intentional" suppression (per detector family; vanished → fixed; "needs author")
- [x] Stage G: scoped revision instructions, span edits, diff, re-check, keep-only-if-better, attempt limits
- [x] Specialist pass order for "polish chapter" (`inkforge polish`)
- [x] Lessons (promote recurring finding → style rule; suggested, only the author adds)
- [~] Golden continuity evaluation (rules 25/25; end to end partial, see below) + critic calibration set (deferred: needs author labels)

**Slice 1 result (2026-10-13, rules only):** on the golden continuity book the rules find **25/25** planted contradictions, each under the rule it was planted for, with **1/10** decoy false positives (d09, "sea-coloured" vs blue — needs judgement, left for model adjudication) and no hits on the clean book. **Caveat:** observations come from the fixture, so this measures the rules, not the pipeline; end-to-end recall will be bounded by fact extraction from prose (next slice).

**Slice 2 (end to end, extraction → quote check → rules):** llama3.2:1b 4/24 recall, 1/10 decoy FP (verification rejected 222 of 258 proposals). llama3.1 8B with prompt v1: 9/24, 2/10 FP; failures were values written as ids, spurious secret mentions, presence not stated → prompt v2, value grounding/anchoring (D37), presence derived from the card. 8B with v2: **17/23** contradictions before the run was stopped (decoys not reached; log in `docs/benchmarks/`). Full evals deferred — the model is replaceable; `scripts/eval_continuity.py` reruns them.

**Agentic layer (2026-10-22):** Stage F (`critique`) and Stage G (`revise`) are Conductor stages — checkpointed per scene, resumable, cancellable; modes `check`, `revise`, `polish`. Stage G guards: edit spliced into its paragraph window + word-diff proof nothing outside changed; rewrite must keep ≥ 40% of the passage's content words and every Codex name in it; kept only if the raising detector stops firing, the evidence words changed, no new high finding or canon contradiction appears and the rubric holds (ε = 1); retried with the reason, then "needs author". Each kept edit is its own unapproved draft version + RevisionProposal + diff. Tested on scripted models incl. cancel-mid-revision + resume and crash-after-save recovery.

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
