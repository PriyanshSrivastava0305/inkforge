# 06 — Memory & Context

Local models have small context windows (8k–32k typical) and degrade well before the limit. The memory system's job is to give each call the *smallest* context that still keeps the story consistent — and to record exactly what was included.

## Effective versions (D34)

Memory is built from each scene's **effective version**: its approved version if it has one, otherwise its active draft (marked *provisional*). The original plan said "on approval", but `run --mode full --yes` never approves drafts (D23), so an overnight run would have had no memory. Everything derived is keyed by the effective text's hash, so approving, editing or redrafting a scene makes its digest, its chapter's and act's digests and its recall chunks stale without any trigger bookkeeping. `inkforge memory status` shows what is stale.

## Three tiers

| Tier | Contents | Size | Updated | Source of truth? |
|---|---|---|---|---|
| **CORE** | POV/tense, style rules, canon rules, protagonist essentials, current story-time anchor, open high-importance promises, characters' current states (location, injuries, held objects, what they know) | ≤ ~800 tokens, hard-budgeted | After each scene approval (deterministic from canon facts + small model summary) | No — derived |
| **DIGEST** | Per-scene summary (≤ 120 words), per-chapter summary (≤ 250), per-act summary (≤ 400) | Hierarchical | When a scene/chapter is approved | No — derived |
| **RECALL** | Exact passages from approved scenes + Codex entries, retrieved on demand | Variable | Indexed on approval | Manuscript files are |

The manuscript and structured records stay the truth; CORE and DIGEST can always be regenerated (`inkforge memory rebuild`). Note: AuthorAgent calls its search tier "ARCHIVAL" and raw drafts "RECALL"; we use the build guide's names.

### Memory consolidation (on demand)

AuthorAgent runs a nightly "sleep-time" job. We make it a command/button: `inkforge memory consolidate` re-summarizes digests, refreshes character state from facts, closes paid promises, and prunes stale CORE lines. Runs as a normal checkpointed job.

## Retrieval

- **Lexical (default):** SQLite FTS5 over approved scene text (chunked by paragraph, ~150–300 tokens, with scene id + char offsets) and Codex.
- **Queries are built deterministically** from the scene card: participant names + aliases, location, objects in `required_facts`, promise descriptions. No model call needed to decide what to search.
- **Semantic (optional, Phase 5 stretch):** local embedding model (e.g. `nomic-embed-text` via Ollama or `bge-small` via sentence-transformers) + FAISS; hybrid score = BM25 ⊕ cosine via reciprocal-rank fusion. Off by default; enabled when the hardware check passes.
- Results are de-duplicated against the recap and digests already included.

## Context builder

*Status (Phase 5):* v2 is built (`src/inkforge/context/builder.py`, prompt `draft.scene@3`). Render order: spec → CORE → codex → digests → chapter plan → RECALL → recap → card. Trimming: RECALL passages lowest-ranked first → digests (acts → earlier chapters → this chapter's scenes) → chapter plan → recap to ~150 tokens → codex to names + voice cards → `ContextTooSmall`. Card and CORE are never trimmed. A missing or stale digest is replaced by an on-the-fly extract. Manifest: `runs/<job>/drafts_<scene>/manifest.json`, with every recalled passage cited as `draft:<scene>@v<n>#<start>-<end>`.

Assembly order (highest priority first; trimming happens from the bottom up):

1. System constraints + output contract (schema / format rules) — **never trimmed**
2. Stage instructions (from prompt registry) — **never trimmed**
3. Book Spec essentials (compressed form)
4. CORE
5. Current unit plan + scene card — **never trimmed**
6. Codex entries for present characters/location (voice cards included for drafting)
7. Recap: last ~400 tokens of the previous scene verbatim + its digest
8. Chapter/act digests (current chapter, previous chapter, current act)
9. RECALL passages (ranked)

### Render order ≠ priority order (prefix caching)

The list above decides what gets **trimmed**. Where each section **sits in the prompt** is decided separately, for speed. On this CPU-only machine ([08](08-model-runtime.md#your-hardware-measured-2026-10-09)), reading a cold prompt runs at ~10 tok/s, while a cached identical prefix runs at ~170 tok/s. Sections are therefore rendered from most stable to most volatile:

```
system + output contract → stage instructions → Book Spec → CORE → Codex (sorted by id)
→ chapter/act digests → RECALL → recap → current unit plan + scene card → task line
```

Consecutive calls (draft → continuation, critic passes on the same scene, cards for neighbouring chapters) then share the longest possible prefix. Rendering must be byte-stable: deterministic ordering, no timestamps or run IDs inside the prompt. The manifest records `prefix_hash` per section boundary so cache hit rates can be checked in `inkforge stats`.

### Budgeting algorithm

```
limit       = model context length (from runtime, or profile override)
reserve_out = max(profile.min_output_tokens, limit * profile.output_fraction)   # default 30%
budget_in   = limit - reserve_out - safety_margin(5%)

tokens counted with the model's tokenizer when available:
  1. llama.cpp server  → POST /tokenize
  2. a locally cached Hugging Face tokenizer for the model family (downloaded at setup)
  3. fallback: calibrated chars-per-token ratio, +10% margin
     (measured 2026-10-09: ~3.7 chars/token for JSON, ~4.5 for prose, Llama-3 family)
  (Ollama 0.34 has no tokenize endpoint, confirmed 404, so it uses 2 or 3. Both installed
   models share the Llama-3 tokenizer, so one cached tokenizer covers both.)

while total(sections) > budget_in:
    drop lowest-ranked RECALL passage
    else shorten digests (act → chapter → none)
    else trim recap to 150 tokens
    else trim Codex entries to name + voice card
    else FAIL with "context too small for this stage" (never silently drop scene card/canon)
```

### Context manifest

Every run writes `runs/<job_id>/manifest.json` listing each section, its sources, token count, and whether it was included, trimmed or dropped (with reason). Plus `prompt.txt` (exact rendered prompt) and `raw_output.txt`. This is how you debug "why did the model forget X?" and how runs become reproducible.

## Memory update triggers

| Event | Updates |
|---|---|
| Scene approved | Scene digest, FTS index, fact extraction → review queue, CORE character states |
| Fact accepted into canon | CORE (if high importance), contradiction re-check on later scenes |
| Chapter fully approved | Chapter digest, act digest refresh |
| Scene card edited | Mark draft `stale`; downstream digests flagged |
| Author edits approved text | Re-index, re-extract facts for that scene, re-check dependants |

## Evaluation (Phase 5 exit)

- Golden fixture with 30 scenes and 25 planted facts. For each later scene that depends on a fact, assert the fact's source passage or canon line is present in the manifest (recall@context ≥ 90%).
- Measure tokens per call vs. naive "last N scenes" baseline.
