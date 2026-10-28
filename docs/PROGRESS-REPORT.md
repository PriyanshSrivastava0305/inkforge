# InkForge — Project Progress Report

**Date:** 27 October 2026
**Project:** InkForge, a local-first story and script writing agent
**Status:** Core engine complete (Phases 0–6 of 12). User interface and extended features not yet started.

---

## 1. Summary

InkForge turns a one-line story idea into a planned, drafted, checked and revised manuscript, running entirely on a single laptop with no cloud services. Over the work so far, the complete back end has been built and tested: planning, drafting, long-book memory, continuity checking and scoped revision. An end-to-end trial on the real local model passed on the target laptop. The remaining work is the user-facing layer (web app, story map, script mode, exports, launcher).

## 2. Objective and constraints

| Item | Decision |
|---|---|
| Purpose | Personal tool for writing novels and scripts |
| Privacy | Strictly local. No cloud calls, no data leaves the machine |
| Models | Only `llama3.1:8b-instruct-q4_K_M` (main writer) and `llama3.2:1b` (fast plumbing tests) |
| Hardware | CPU-only laptop, 16 GB RAM (8B model ≈ 3 words/second) |
| Version control | Local git only; nothing is pushed anywhere |
| Stack | Python 3.12, Pydantic, Typer CLI, SQLite (index), Ollama |

## 3. How it is being built

The plan has 12 phases. Each phase ends with automated tests, documentation and a local commit. Design principles:

- **Code verifies, the model only proposes.** IDs are minted by code; every quote a model cites is checked against the text; unsafe output is rejected.
- **Files are the source of truth.** Every artifact is versioned; nothing is overwritten silently.
- **The author decides.** Nothing becomes canon or approved without an explicit user action, and every such action is logged.
- **Resumable and cancellable.** Each job checkpoints per step; an interruption loses at most one model call.
- **Model-independent.** Everything is tested on a scripted fake model, so the real model can be replaced later without touching the pipeline.

## 4. Progress by phase

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundations, model benchmark | Complete |
| 1 | Domain model and workspace storage (versioning, locking, backup) | Complete |
| 2 | Model router and prompt registry | Complete |
| 3 | Planning pipeline: spec → design → outline → scene cards | Complete |
| 4 | Scene drafting, approvals, Markdown export | Complete |
| 5 | Long-book memory (summaries, canon facts, recall) | Complete* |
| 6 | Quality engine: checking and revision | Complete* |
| 7 | Local web app (authoring and quality views) | Not started |
| 8 | Story map, timeline, Codex editor | Not started |
| 9 | Script / screenplay mode | Not started |
| 10 | Further export formats and hardening | Not started |
| 11 | Launcher and daily-use polish (v1.0) | Not started |

\* Two items deliberately deferred: embeddings-based recall (lexical recall already met its target) and the full real-model evaluations plus critic calibration set (the latter needs about 50 findings labelled by the author).

**Size:** about 12,700 lines of source and 5,900 lines of tests in 17 local commits.

## 5. What the system can do today

1. **Plan** — an idea becomes a book spec, story design, outline and scene cards, pausing at approval gates.
2. **Draft** — scenes are written with memory of the earlier book; model artifacts (copied prompt text, invented titles) are stripped automatically.
3. **Check** — deterministic rules (repetition, stock phrases, point of view, tense, length, character presence), fact extraction with verified quotes, canon-conflict rules (single-valued facts, death, knowledge, presence, object state, age/date, promises, travel), model adjudication of ambiguous cases, a craft score on eight dimensions, and per-character voice checks.
4. **Revise** — only findings the author accepted are fixed. Each edit is confined to one paragraph, proven by a diff; it is kept only if the problem is gone, nothing new broke and the craft score did not drop; otherwise it is retried and then handed back to the author.
5. **Review** — extracted facts enter a review queue; only the author's accept or retcon command changes canon. Recurring problems can be promoted to style rules ("lessons") shown to the drafting model.

## 6. Verification results

- The automated test suite (no model required) passes in full, including cancel-and-resume and crash-recovery tests for revision.
- **Canon-conflict rules:** all 25 planted contradictions in the golden test book were found, with 1 of 10 decoys incorrectly flagged.
- **End-to-end trial on the real 1B model** (3 chapters): completed all stages in 40 minutes. 3 scenes drafted (503, 570, 686 words); 22 issues found; 2 edits kept and 3 unsafe rewrites correctly refused; no canon changed; preview exported.
- **Earlier real-model runs:** a 9,571-word novella drafted offline with the 1B; a short story drafted offline with the 8B.
- The trial exposed and led to fixes for several defects (for example, an edit that only removed punctuation was being counted as a fix).

## 7. Known limitations and risks

- The 1B model writes weak prose; it is used only to test the plumbing.
- The 8B model has not yet been trial-run through the full check and revise loop. A partial run found 17 of 23 planted contradictions.
- The 8B is slow on this CPU (about 13 minutes per scene with memory on); long runs need the laptop plugged in.
- Prose quality and critic precision on the 8B are unmeasured pending the calibration set.

## 8. Next steps

1. Run the full trial on the 8B model to assess real writing and revision quality (1–2 hours, laptop plugged in).
2. Phase 7: local web app — authoring views, run console, then quality desk and fact review.
3. Phase 8: story map, timeline and Codex editor.
4. Phase 9: script mode.
5. Phase 10: additional exports (docx, epub) and hardening.
6. Phase 11: launcher and polish to reach v1.0.
