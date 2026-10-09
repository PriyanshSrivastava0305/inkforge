# InkForge — Project Plan & Documentation

InkForge is a **local-first story and script writing agent**. It takes an author from a one-line idea to a finished manuscript (novel, novella, short story) or script (screenplay, stage play) through a staged, checkpointed pipeline, with a continuity engine and critics that keep long works consistent. Every model call runs on the author's own machine.

This folder (`docs/`) is the working plan. It is built on top of [`reference/INKFORGE_BUILD_GUIDE.md`](reference/INKFORGE_BUILD_GUIDE.md) and the architecture diagrams in [`reference/`](reference/), and on a review of three reference agents (AuthorAgent, GOAT-Storytelling-Agent, OpenWrite).

> **Status:** Phase 0 ✓ and Phase 1 ✓ (domain contracts & workspace storage). Next: Phase 2 (model router & prompt registry). Last updated 2026-10-09.
>
> **Fixed context:** personal tool, strictly local, CPU-only laptop (i7-1165G7, 16 GB). Models: `llama3.1:8b-instruct-q4_K_M` (default), `llama3.2:1b` (fast tests). See D16–D18 in [12](12-decisions-and-open-questions.md).

## How to read this folder

| # | Document | What it answers |
|---|---|---|
| 01 | [Vision & scope](01-vision-and-scope.md) | What we are building, for whom, what is in/out of v1 |
| 02 | [Reference analysis](02-reference-analysis.md) | What we take from each reference repo, and what we deliberately don't |
| 03 | [Architecture](03-architecture.md) | Components, data flow, trust model, stack, repo layout, security |
| 04 | [Data model & storage](04-data-model.md) | Every entity, its fields, the on-disk workspace layout, versioning |
| 05 | [Story pipeline](05-pipeline.md) | Stages A–H in detail, for prose **and** script forms |
| 06 | [Memory & context](06-memory-and-context.md) | CORE / DIGEST / RECALL, context builder, token budgeting |
| 07 | [Quality engine](07-quality-engine.md) | Continuity/canon checks, critics, revision loop |
| 08 | [Model runtime](08-model-runtime.md) | Ollama / llama.cpp router, profiles, structured output, hardware |
| 09 | [UI / UX](09-ui-ux.md) | Web workbench views, CLI commands, interaction rules |
| 10 | [**Roadmap — phases**](10-roadmap-phases.md) | The build plan: 12 phases with tasks, deliverables and exit criteria |
| 11 | [Testing & acceptance](11-testing-and-acceptance.md) | Test layers, golden fixtures, release gates |
| 12 | [Decisions & open questions](12-decisions-and-open-questions.md) | Decision log (ADRs) and what still needs your call |

Start with **01**, then jump to **10** for the phased plan. Everything else is reference you'll come back to while building each phase.

## The plan at a glance

```
Phase 0  Foundations & model spike            ┐
Phase 1  Domain contracts & workspace storage │  Engine (CLI only)
Phase 2  Model router & prompt registry       │
Phase 3  Planning pipeline (A–D) + Conductor  │
Phase 4  Drafting (E) + Markdown export       ┘  ★ MVP: idea → book, offline, CLI
Phase 5  Long-book memory                     ┐
Phase 6  Quality engine (F–G)                 ┘  ★ Beta engine: consistent long books
Phase 7  Web app: 7a authoring, 7b QA views   ┐
Phase 8  Story map, timeline & Codex UI       ┘  ★ Workbench
Phase 9  Script mode (screenplay / stage play)   ★ Story + script
Phase 10 Export & hardening (H)               ┐
Phase 11 Launcher & daily-use polish          ┘  ★ v1.0 release
```

The order follows one rule from the build guide: **data contracts and a checkpointed pipeline before any UI.** A pretty story map can't rescue weak state management.

## Core principles (the short version)

1. **Local-only runtime.** App, data, indexes and model inference stay on the machine; offline after setup.
2. **Author owns canon.** AI output is a proposal. Nothing becomes canon, gets overwritten, or is exported without author approval.
3. **Every stage is a checkpoint.** Any stage can be run alone, edited by hand, retried, or resumed.
4. **Files are the source of truth.** Plain Markdown/JSON on disk; SQLite and search indexes are rebuildable caches.
5. **Evidence over vibes.** Every critique finding points at the exact passages it's about.
6. **Deterministic code owns state; the LLM proposes.** Schemas, IDs, versions, exports and audit are never model-controlled.

## Naming

The working name is **InkForge** (from the build guide). The CLI is `inkforge`, the Python package is `inkforge`. Rename freely before Phase 0 ends; after that it touches imports.
