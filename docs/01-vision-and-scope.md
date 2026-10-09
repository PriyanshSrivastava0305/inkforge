# 01 — Vision & Scope

## Problem

LLMs can write a good page. They are bad at writing a good *book* or *script*: they forget who knows what, drift character voices, contradict established facts, lose setups before their payoffs, and produce generic prose. Existing tools split the problem:

- **Planning tools** (OpenWrite) give great structure and a visual map but leave the writing and checking to the author.
- **Generation pipelines** (GOAT) produce a whole book top-down but have no memory beyond the previous scene and no quality control.
- **Autonomous agents** (AuthorAgent) do orchestration, memory and critique, but lean on cloud models and a broad publishing feature set.

InkForge combines the three into one local system where the author plans visually, the agent generates in stages, and a quality engine keeps the result consistent.

## Who it's for

**You, on your own machine.** InkForge is a personal tool, not a product. There's no distribution, other users, accounts or telemetry (decision D17). You're both the author using it and the developer experimenting with local models, prompts and long-form evaluation.

- Target machine: Windows 11 laptop, i7-1165G7, 16 GB RAM, CPU-only inference ([08](08-model-runtime.md#your-hardware-measured-2026-10-09)).
- macOS/Linux support, installers and "someone else can install it" polish are out of scope.

## Forms supported

The domain model is **form-aware from day one** (see [04](04-data-model.md#form-profiles)), even though script output ships in Phase 9.

| Form | Unit hierarchy | Output | Ships |
|---|---|---|---|
| Novel / novella | Book → Act → Chapter → Scene | Prose | MVP (Phase 4) |
| Short story | Story → Scene | Prose | MVP (Phase 4) |
| Screenplay (feature) | Script → Act → Sequence → Scene | Fountain / screenplay PDF | Phase 9 |
| TV episode | Episode → Act (teaser/tag) → Scene | Fountain | Phase 9 |
| Stage play | Play → Act → Scene | Stage-play format | Phase 9 (stretch) |

## Goals for v1.0

1. Idea → complete first draft for a 60–90k-word novel or a 90–120-page screenplay, run entirely offline.
2. Every stage artifact (spec, design, outline, scene cards, drafts, findings) is a versioned, editable file.
3. A long run can be killed at any point and resumed from the last valid checkpoint with no corruption.
4. Continuity findings cite both conflicting passages; no canon change without author confirmation.
5. A local web workbench: outline, story map, Codex, manuscript editor, run console, quality desk.
6. Export: Markdown, DOCX, EPUB (prose); Fountain and PDF (script), each with a preflight report.
7. One-command launch on your Windows machine (`inkforge up`) with a `doctor` check of the local models.

## Non-goals for v1.0

- Cloud model providers or any network call at runtime. **Strictly local, decided** (D2, Q1 closed). There's no opt-in cloud path either, and none will be designed for.
- Distribution: installers, other users, licensing work, cross-platform packaging.
- Publishing/marketing suite (launch planners, ad optimizers, BookBub, author sites) — AuthorAgent has these; they are out of scope.
- Cover generation, audiobook/TTS, translation.
- Multi-user accounts, real-time collaboration, hosted deployment.
- Fine-tuning models. (Model choice is configuration; LoRA training can be a later research track.)
- Claiming output is publisher-ready. Exports always say a human proofread is required.

## Constraints (non-negotiable, from the build guide)

| Constraint | What it means in practice |
|---|---|
| Local-only runtime | API and model server bind to `127.0.0.1`; no telemetry; no remote fallback |
| Offline after setup | Dependencies and weights downloaded in an explicit setup step; runtime tested in airplane mode |
| Author in control | Canon changes, destructive ops and exports need review or explicit confirmation |
| Long-book reliability | Every stage writes a checkpoint; can be run independently, edited, retried, resumed |
| Inspectable artifacts | Outlines, scene cards, findings, prompts, context manifests and diffs are persisted |
| Portable data | Markdown/JSON is the source of truth; DB and indexes are rebuildable |

## Success metrics

| Metric | Target for v1.0 | How measured |
|---|---|---|
| End-to-end offline run | 100% of golden fixtures complete | Offline acceptance suite ([11](11-testing-and-acceptance.md)) |
| Resume correctness | 0 corrupted artifacts across 50 random kill points | Chaos/resume test |
| Seeded contradiction recall | ≥ 80% of planted contradictions flagged | Golden continuity fixture |
| Finding precision | ≥ 60% of high-severity findings judged valid by author | Manual review of fixture runs |
| Schema-valid structured output | ≥ 95% first try, 100% after retries/repair | Router metrics |
| Throughput (llama3.1 8B Q4, your CPU) | 2,000-word scene draft ≤ 25 min wall time, incl. continuation; ≥ 2.5 tok/s sustained | Benchmark harness |
| Prefix-cache reuse | ≥ 60% of prompt tokens served from cache across a chapter's draft + critique calls | Manifest `prefix_hash` stats |

Throughput is set from the 2026-10-09 smoke test (~3 tok/s generation, ~10 tok/s cold prompt reading). At that speed a full novel draft is a multi-night batch job. The plan is shaped around that: checkpoints, resume, per-chapter runs and a small context.
