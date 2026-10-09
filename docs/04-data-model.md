# 04 — Data Model & Storage

Pydantic models in `packages/domain` are the single contract between stages, storage, API and UI. Files on disk are the source of truth; SQLite is an index that can be deleted and rebuilt with `inkforge reindex`.

## Conventions

- **IDs** are minted by code, never by the model. Human-readable, stable, prefixed: `ch03`, `ch03-sc02`, `char-mira`, `loc-glass-orchard`, `obj-brass-key`, `lore-tithe-law`, `prom-007`, `fact-0142`, `find-0031`. Renumbering on reorder is *not* allowed — order lives in an explicit `order` field so IDs stay stable.
- **Versions** are immutable. Every regenerate/edit creates `vN+1`; an artifact has one `active_version` pointer and one optional `approved_version`.
- **Hashes:** each version stores `sha256` of its content and the hashes of its inputs, so staleness can be detected ("scene card changed since this draft was written").
- **Timestamps** UTC ISO-8601. **Schema version** on every file (`"schema": "inkforge/scene-card@1"`) for migrations.
- **Status enum** used across artifacts: `draft → proposed → approved` plus `stale` (inputs changed) and `archived`.

## Form profiles

A `FormProfile` decides unit names, hierarchy depth, stage templates and exporters. Stored in the project, loaded from `packages/domain/forms/*.toml`.

```toml
# forms/novel.toml
id = "novel"
units = ["act", "chapter", "scene"]
output = "prose"
default_target_words = 80000
exporters = ["markdown", "docx", "epub"]

# forms/screenplay.toml
id = "screenplay"
units = ["act", "sequence", "scene"]
output = "script"          # Fountain-based
default_target_pages = 110 # ≈ 1 page/min
exporters = ["fountain", "pdf", "fdx"]
```

Stages ask the profile, not hard-coded "chapter", for unit names. This is what lets script mode (Phase 9) be additive.

## Entities

### Project
`id, title, form (FormProfile id), genre, status, target_length, created_at, updated_at, settings (model profiles, approval gates, budgets), schema`

### BookSpec (Stage A)
`premise, logline, genre, subgenres, audience, tone, pov (first/third-limited/omniscient/second; n/a for scripts), tense, setting {place, time}, themes[], content_boundaries[], comparable_titles[], target_length, narrative_promises[], style_notes, custom_fields{}`
Plus `original` (author's raw input, never overwritten) and `enhanced` (model refinement) shown as a diff.

### StoryDesign (Stage B)
`dramatic_question, theme_statement, reader_promise, structure_template (e.g. three-act, save-the-cat, hero's-journey, kishōtenketsu), acts[] {id, purpose, turning_point}, arcs[] {character_id, start_state, end_state, beats[]}, promise_ids[] (→ PlotPromise registry), ending_constraints`

StoryDesign does **not** embed setups/payoffs. Stage B proposes promises; accepted ones are written to the promise registry and the design keeps only their IDs.

### PlotPromise (setup/payoff, "Chekhov's gun")
`id, description, setup_ref (scene id or planned beat), payoff_ref | null, status (open/paid/abandoned/intentional-surprise), importance, created_by_stage, history[] {ts, actor, from_status, to_status}`

**Single source of truth:** `canon/promises.json` is the only place promises live. Stage B, scene cards (`promises_touched[]`), chapter plans and the quality engine all reference promises by ID. Status changes go through the registry (audited), never by editing a design or card.

### Outline (Stage C, structure only)
`acts[] {id, order}, chapters[] {id, order, act_id, title}`
The outline file holds only ordering and titles. Each chapter's content lives in its own versioned ChapterPlan file (below), so editing one chapter doesn't invalidate the others.

### ChapterPlan (Stage C) — generic "unit plan", one file per chapter
`id, act_id, title, purpose, conflict, turning_point, outcome, causal_link_prev, causal_link_next, characters[], required_info[], pacing_note, target_length, promises_touched[]`
(`order` lives in the Outline, not here.)

### SceneCard (Stage D)
`id, chapter_id, order, pov_character_id, location_id, time {story_datetime?, time_of_day?, elapsed_since_prev?, label}, temporal_mode (linear|flashback|flashforward|dream|unreliable; default linear), participants[], items_present[], want, obstacle, change, story_value, value_charge (+/- → +/-), mood, beats[], concrete_event, turn, exit_condition, required_facts[], promises_touched[], target_length, notes`
- `order` is **narrative order** inside the chapter (the order the reader sees). Chronological placement lives in the Timeline (see [Story time](#story-time)).
- `temporal_mode` tells the quality engine which rules to relax ([07](07-quality-engine.md#3-canon-conflict-rules)). Non-linear scenes must also be placed explicitly on the Timeline.
Script additions (Phase 9): `slugline {int_ext, location, time_of_day}`, `page_estimate`.

### SceneVersion (Stages E/G)
`scene_id, version, text_path, word_count, model_profile, model_name, prompt_id@version, context_manifest_path, input_hashes{}, parent_version, created_at, created_by (system|user), status`

### Character (Codex)
`id, name, aliases[], role, age/birthdate, appearance, goals, fears, secrets, traits[], voice_card {diction, sentence_length, verbal_tics, vocabulary_level, taboo_words, sample_lines[]}, knowledge[] (fact ids + since scene), relationships[] {other_id, kind, since}, arc_ref`

### Location / LoreItem (Codex)
`id, name, aliases[], description, rules[], first_introduced (scene id), parent_location, source_refs[]`

### Item (Codex) — objects that matter to continuity
`id, name, aliases[], description, kind (weapon|document|key|artifact|vehicle|other), significance (prop|plot|macguffin), unique (bool), first_introduced (scene id), promise_ids[], source_refs[]`
Items are what object-state rules track. Who holds an item, where it is and its condition (`intact`, `broken`, `lost`, `destroyed`) are **facts** with time validity, not fields on the item, so they can change across the story. Only items with `significance ≥ plot` are tracked by default. Scenery doesn't need an entry.

### Story time
Two orders exist and must not be confused:

| Order | Meaning | Where it lives |
|---|---|---|
| **Narrative order** | The sequence the reader experiences | Outline (chapter `order`) + SceneCard `order` |
| **Story order** | When events actually happen in the world | `timeline/timeline.json` |

`timeline.json` holds `entries[] {scene_id, story_datetime?, label, concurrent_with?: scene_id}`. The list order **is** chronological order. Code derives a scene's `story_index` from its position, and `concurrent_with` marks two scenes as happening at the same time. The file is versioned and audited like any other artifact.
- Stage D places new linear scenes after the previous scene by default. Flashbacks, flashforwards and dreams need explicit placement (proposed by the model as "after `<scene_id>`" and validated by code, or set by the author).
- `story_datetime` is optional. Rules that only need ordering (knowledge, presence, object state, death) use `story_index`. Arithmetic rules (ages, dates, travel time) run only when both endpoints have absolute datetimes.
- Reordering scenes in the outline never changes story order, and the reverse holds too. The Timeline view (Phase 8) edits this file.

### Fact (canon)
`id, subject_id, predicate, value, valid_from {scene_id}, valid_to {scene_id}?, source_ref {scene_id, version, char_span}, origin (author|extracted), confidence, canon_status (candidate|canon|rejected|retconned)`
- Validity bounds are always **scene references**. Code resolves them to `story_index` through the Timeline, so all temporal comparisons use one ordering. An author-established fact with no scene uses `valid_from: null` (true from the start).
- `subject_id` must be a Codex entity: `char-*`, `loc-*`, `obj-*` or `lore-*`.
Examples: `(char-mira, eye_color, "grey", from ch02-sc01)`, `(obj-brass-key, held_by, char-tom, from ch04-sc01 to ch07-sc03)`.

### Finding (Stage F)
`id, scene_id, category (continuity|timeline|knowledge|voice|motivation|pacing|clarity|prose|promise|format), severity (low|med|high|blocker), claim, evidence[] {scene_id, version, span, quote}, conflicting_fact_id?, affected_entities[], detector (rule:<name>|critic:<name>), confidence, is_hypothesis, status (open|accepted|dismissed|intentional|fixed), resolution_note`

### RevisionProposal (Stage G)
`id, scene_id, base_version, finding_ids[], instruction, diff_path, candidate_version, scores_before{}, scores_after{}, decision (pending|accepted|rejected|auto-discarded)`

### Job / Checkpoint
`job_id, project_id, stage, target_id, status (queued|running|awaiting_approval|succeeded|failed|cancelled), attempts, input_hashes, output_refs[], started_at, finished_at, error_summary, events_path`

### AuditEvent (append-only JSONL)
`ts, actor (user|system), operation, target, before_ref, after_ref, job_id?`

### ContextManifest
`job_id, model, context_limit, budget {input, output_reserved}, sections[] {name, source_refs[], tokens, included|trimmed|dropped, reason}, total_tokens`

## Workspace layout on disk

```text
<workspace>/
└── projects/
    └── the-glass-orchard/
        ├── project.json
        ├── spec/book_spec.v1.json, book_spec.v2.json, book_spec.meta.json   # meta = pointers + version records
        ├── design/story_design.vN.json
        ├── outline/
        │   ├── outline.vN.json                # structure only: act/chapter ids, order, titles
        │   └── chapters/ch03/plan.vN.json     # one versioned ChapterPlan per chapter (+ plan.meta.json)
        ├── timeline/timeline.vN.json          # story (chronological) order
        ├── scenes/
        │   └── ch01-sc02/
        │       ├── card.vN.json
        │       ├── draft.v1.md, draft.v2.md
        │       └── card.meta.json, draft.meta.json   # versions, active/approved pointers, input hashes
        ├── codex/{characters,locations,lore,items}/<id>.json
        ├── canon/facts.jsonl, review_queue.jsonl, promises.json   # promises.json = sole promise registry
        ├── memory/core.json, digests/<ch>.json
        ├── quality/findings.jsonl, revisions/<id>.diff
        ├── runs/<job_id>/{job.json, events.jsonl, manifest.json, prompt.txt, raw_output.txt}
        ├── exports/<timestamp>/{book.md, book.docx, preflight.json}
        ├── backups/<timestamp>.zip
        ├── audit.jsonl
        └── index.sqlite                       # rebuildable
```

"Active pointer" files are small JSON (`{"active": 2, "approved": 1}`) rather than symlinks — symlinks are painful on Windows.

## Staleness: what each artifact depends on

Staleness is computed from **declared inputs** and their hashes, never from "anything upstream changed". Each version records `input_hashes{ref: sha256}` for exactly these inputs:

| Artifact | Declared inputs (hashed) |
|---|---|
| StoryDesign | BookSpec |
| Outline | BookSpec, StoryDesign |
| ChapterPlan `chN` | StoryDesign, Outline *entry* for `chN` (title/act, not `order`); soft: previous/next neighbour as `chM@vK` tokens |
| SceneCard in `chN` | ChapterPlan `chN`, Codex entries for its POV/participants/location |
| Draft | its SceneCard, voice cards of present characters |

**Neighbour context is "soft".** It never makes anything stale, only *review suggested*: the neighbour's id changed (reorder/insert), or its `purpose`/`outcome` changed since the baseline. If a neighbour had no plan yet when a chapter was written, the neighbour's first plan becomes the baseline. Chapters are generated in sequence, so a neighbour *appearing* is not a change.

Editing chapter 3 therefore marks only chapter 3's cards stale, plus a "review suggested" flag on chapters 2 and 4 if 3's `purpose`/`outcome` changed. It doesn't mark the whole book stale. Reordering chapters changes only the Outline, so it never makes chapter content stale.

## SQLite index

Tables mirror the entities for querying (`projects, chapters, scenes, scene_versions, timeline, characters, items, facts, findings, jobs, promises`) plus FTS5 virtual tables (`scene_text_fts`, `codex_fts`). `inkforge reindex` drops and rebuilds everything from files; this is tested.

## Migrations

- File schema: `schema` field + migration functions `migrate_scene_card_1_to_2()`; applied lazily on read and rewritten on next save.
- DB schema: no migrations. A `schema_version` mismatch (or a missing/corrupt file) triggers an automatic rebuild from files ([ADR-0003](adr/0003-sqlite-index-stdlib.md)).
