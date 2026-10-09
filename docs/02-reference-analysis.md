# 02 — Reference Analysis

Three open-source projects informed this design. This page records what each one does, what we borrow, and what we deliberately leave out, so later decisions can be traced back. Details come from each repo's README and the diagrams in `reference/`; internals not described there are marked as unknown.

## AuthorAgent — `github.com/Ckokoski/AuthorAgent`

**What it is.** A local-first Node/TypeScript agent (MIT) that runs a book from idea to publish-ready files, with a web dashboard, skills, memory tiers and a heavy quality stack. Supports Ollama plus several cloud providers.

**Notable pieces**
- Conductor that dispatches workers ("Orchestra" view); full-book or single-stage modes (Just Outline / Just Write / Just Polish / Just Format).
- Skills as `skills/*/SKILL.md` (YAML frontmatter + markdown).
- Memory: **CORE** (budgeted digest always in prompt: chapter state, characters in scene, open threads, style fingerprint), **ARCHIVAL** (full-text search), **RECALL** (raw drafts on disk). Nightly "sleep-time" consolidation.
- Contradiction detection against an entity DB, with findings citing chapter text and the conflicting fact.
- Per-character persona agents that critique their own dialogue.
- Revision orchestrator with specialist passes (continuity, voice, craft, anti-slop).
- GEPA-style judge → diagnose → revise → re-judge loop; **keep a candidate only if it measurably improves**.
- Synthetic reader panels; lessons learned from recurring findings get injected into future prompts.
- Plot-promise (Chekhov's gun) tracking; 10 story structures with outline checking; series bible.
- 127.0.0.1 binding, JSONL audit log, confirmation gates, 410-test vitest suite.

**We borrow:** Conductor + single-stage modes, skills registry format, memory tiers, evidence-chained findings, persona voice critics, specialist revision passes, keep-only-if-better loop, plot-promise tracking, structure templates, lessons-learned injection, audit log, test discipline.

**We leave out:** cloud providers and cost-tier routing, publishing/marketing suite, covers, audiobook/TTS, translation, Telegram bridge, encrypted key vault (no keys needed locally). Sleep-time consolidation becomes an *on-demand* "consolidate memory" job rather than a nightly scheduler.

## GOAT-Storytelling-Agent — `github.com/GOAT-AI-lab/GOAT-Storytelling-Agent`

**What it is.** A Python library that generates a story top-down with a `StoryAgent` class, designed around a fine-tuned GOAT-70B-Storytelling model served via HF TGI or llama.cpp.

**Pipeline (`generate_story`)**
1. `init_book_spec(topic)` — Genre, Place, Time, Theme, Tone, POV, Characters, Premise (+ custom fields)
2. `enhance_book_spec`
3. `create_plot_chapters` — chapters grouped into acts
4. `enhance_plot_chapters`
5. `split_chapters_into_scenes` — Characters, Place, Time, Event, Conflict, Story value, Story value charge, Mood, Outcome
6. `write_a_scene` — loops act/chapter/scene, passing the previous scene
7. `continue_a_scene` — continues when a scene overruns the context window

**We borrow:** strict top-down decomposition, the generate→enhance pair per planning level, the scene-card field set (especially *story value* and *charge*, which come from McKee-style scene design), independent entry points per stage, safe continuation on truncation, backend abstraction.

**We improve on:** context is only the previous scene (we add CORE/DIGEST/RECALL); no built-in checkpointing (we persist after every stage); no quality control (we add the quality engine); a 70B fine-tune is the default (we target 7–14B quantized models that run on a laptop, with profiles for bigger ones).

## OpenWrite — `github.com/ilrein/openwrite`

**What it is.** An AGPL-3.0 TypeScript writing platform: React 19 + Tiptap editor, Hono API on Cloudflare Workers, D1 (SQLite) via Drizzle, BYO-key AI assistant (incl. Ollama).

**Notable pieces**
- Story map: expand one sentence level by level (premise → acts → chapters → scenes) as connected nodes on a canvas with auto-layout; **promote a chapter node straight into the manuscript**.
- Codex: characters, locations, lore, plot points as structured, per-project data.
- Tiptap editor with autosave, live word counts, per-project progress targets.
- Project types: novel, trilogy, series, short-story collection, **screenplay**.
- Concurrent-edit detection.

**We borrow:** progressive one-level-at-a-time expansion on the map, node-to-manuscript promotion, Codex as first-class structured data, Tiptap editor, word-count/progress targets, form-aware project types, concurrent-edit detection.

**We leave out:** Cloudflare deployment, auth/accounts, hosted mode, BYO cloud keys. **License note:** OpenWrite is AGPL-3.0 — use it for ideas only; do not copy code into InkForge unless InkForge is also AGPL.

## Licensing summary

| Repo | License | Safe to copy code? |
|---|---|---|
| AuthorAgent | MIT | Yes, with attribution (but it's TypeScript; we're Python on the backend) |
| GOAT-Storytelling-Agent | Check repo LICENSE before reuse | Ideas only until confirmed |
| OpenWrite | AGPL-3.0 | Ideas only, unless InkForge adopts AGPL |

Pick InkForge's own license in Phase 0 (see [12](12-decisions-and-open-questions.md)).

## Feature traceability

| Capability | From | InkForge doc | Phase |
|---|---|---|---|
| Top-down staged pipeline | GOAT | [05](05-pipeline.md) | 3–4 |
| Scene cards w/ story value | GOAT | [04](04-data-model.md), [05](05-pipeline.md) | 3 |
| Safe continuation | GOAT | [05](05-pipeline.md#stage-e--draft-scene) | 4 |
| Conductor + single-stage modes | AuthorAgent | [03](03-architecture.md), [09](09-ui-ux.md) | 3 |
| Skills / prompt registry | AuthorAgent | [08](08-model-runtime.md#prompt--skill-registry) | 2 |
| CORE / DIGEST / RECALL | AuthorAgent | [06](06-memory-and-context.md) | 5 |
| Continuity engine | AuthorAgent | [07](07-quality-engine.md) | 6 |
| Voice critics, revision loop | AuthorAgent | [07](07-quality-engine.md) | 6 |
| Plot promises | AuthorAgent | [07](07-quality-engine.md) | 6 |
| Story map + promotion | OpenWrite | [09](09-ui-ux.md) | 8 |
| Codex | OpenWrite | [04](04-data-model.md), [09](09-ui-ux.md) | 1, 8 |
| Screenplay project type | OpenWrite | [05](05-pipeline.md#script-mode) | 9 |
| DOCX / EPUB export | AuthorAgent | [05](05-pipeline.md#stage-h--approve--export) | 10 |
