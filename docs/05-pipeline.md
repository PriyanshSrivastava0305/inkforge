# 05 — Story Pipeline

Eight stages, run top-down. Each stage is a class with the same contract and can be run alone against saved inputs.

```python
class Stage(Protocol):
    name: str  # "scene_cards"
    profile: str  # model profile: planner | prose | critic
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    approval_gate: bool  # default from project settings

    def check_inputs(self, ctx: StageContext) -> list[Problem]: ...
    def build_prompt(self, ctx: StageContext) -> PromptBundle: ...  # uses context builder + registry
    def generate(self, prompt: PromptBundle, router: ModelRouter) -> RawOutput: ...
    def parse_and_validate(self, raw: RawOutput) -> Output | ValidationFailure: ...
    def persist(self, out: Output, meta: RunMeta) -> list[ArtifactRef]: ...
```

Everything except `generate` is deterministic. Failure policy for every stage: up to *N* (default 3) attempts — attempt 2 adds the validation error to the prompt ("repair"), attempt 3 lowers temperature. If all fail, the job fails and the previous valid artifact stays active.

## Modes

Borrowed from AuthorAgent: the author can run the **full pipeline** (with approval gates) or one mode:

| Mode | Stages | Use |
|---|---|---|
| Plan only | A → D | Get a full outline and scene cards to write yourself |
| Draft | E (for selected scenes) | Write from existing cards |
| Polish | F → G | Critique/revise text, including author-written text imported in |
| Export | H | Build output files |

Every planning stage (A–D) has an **enhance** variant (GOAT's generate→enhance pair): a second pass that critiques and improves the artifact, shown to the author as a diff.

## Stage A — Book Spec

- **In:** author's idea (one line to a page), form, optional fields.
- **Out:** `BookSpec` (`spec/book_spec.vN.json`), with `original` preserved.
- **Prompt:** fill missing fields; never contradict author-provided fields; list assumptions made.
- **Checks:** required fields present; target length sensible for the form; content boundaries non-empty or explicitly "none".
- **Gate:** approve spec (default on).

## Stage B — Story Design

- **In:** BookSpec, chosen structure template (skill), form profile.
- **Out:** `StoryDesign` (referencing promises by ID) + proposed `PlotPromise[]` written to the promise registry `canon/promises.json` on approval, seed Codex entries for main characters and plot-significant items (as *candidates*).
- **Checks:** every act has a turning point; each major arc has start≠end state; every payoff links to a setup or is flagged `intentional-surprise`; ending satisfies the dramatic question.
- **Gate:** approve design + accept/reject seeded characters into Codex.

## Stage C — Chapter Plan (unit plan)

- **In:** Spec, Design, Codex.
- **Out:** `outline/outline.vN.json` (structure: chapter ids, order, titles) + one `outline/chapters/<id>/plan.vN.json` per chapter. Regenerating or editing one chapter creates a new version of that chapter's file only.
- **Generation strategy:** one act at a time (keeps prompts small on 8B models), then a whole-outline enhance pass using only titles + one-line purposes.
- **Checks (deterministic):** chapter count fits target length; no two chapters share an identical purpose (fuzzy match); every chapter has a causal link to its neighbour; every open promise is touched by some later chapter; every main character appears at least once per act (configurable).
- **Gate:** approve outline.

## Stage D — Scene Cards

- **In:** one ChapterPlan + neighbours' summaries + Codex.
- **Out:** `SceneCard[]` per chapter + Timeline placements. Linear scenes are appended after the previous scene in story order. For non-linear scenes (`temporal_mode ≠ linear`), the model proposes "after `<scene_id>`" from an allowed ID list; code validates it and inserts the entry.
- **Checks:** POV character exists in Codex; location exists or is proposed as new Codex candidate; items in `items_present` exist or are proposed; `want/obstacle/change` all present; value charge actually changes (GOAT's "story value charge"); scene lengths sum to chapter target ±20%; every non-linear scene has a Timeline placement.
- **Gate:** per chapter (default off for speed; author can edit cards anytime).

## Stage E — Draft Scene

- **In:** SceneCard, context from the context builder ([06](06-memory-and-context.md)): CORE, relevant Codex (with voice cards for present characters), chapter digest, recap of previous scene's ending, retrieved passages. *Before Phase 5 (no digests/CORE yet):* the chapter plan's `purpose`/`conflict`/`outcome` plus the one-line `concrete_event` of earlier scene cards in the chapter stand in for the chapter digest, and CORE/RECALL are omitted.
- **Out:** `SceneVersion` (`draft.vN.md`) + context manifest.
- **Length & truncation:** request ~target length. If output hits `max_tokens` or ends mid-sentence → **continuation**: send the last ~300 tokens as the anchor, ask to continue; strip any overlap by longest-common-suffix/prefix match before appending. Stop when the exit condition is reached or a hard cap is hit.
- **Checks:** length within tolerance; all `participants` appear; POV not broken (simple pronoun/name heuristics for first-person); no unknown capitalized names that look like new characters (flag as candidate, not error); no meta text ("As an AI…", "Chapter 3:" headers inside scene).
- **Gate:** none by default — drafts go straight to critique; approval happens at H.
- **Staleness:** if the scene card's hash changes later, the draft is marked `stale`.

## Stage F — Critique

Detailed in [07](07-quality-engine.md). Order: deterministic checks → fact extraction into review queue → model critics (continuity adjudication, voice per character, craft rubric). Output: `Finding[]`, each with evidence spans.

## Stage G — Revise

- Accepted findings → small scoped **edit instructions** ("In paragraph 4, Mira cannot know about the letter yet; replace her reference with…").
- Model produces a revised scene (or a revised span for local edits); code computes a **diff** (word-level) and stores a `RevisionProposal`.
- Re-run the checks that produced the findings + a quick rubric. **Keep only if better** (AuthorAgent/GEPA idea): no new high-severity findings and rubric not worse. Otherwise auto-discard and record why.
- Loop limit: max attempts per finding (default 2), stop on plateau or author rejection.
- Author accepts/rejects diffs; accepted → new SceneVersion.

## Stage H — Approve & Export

- Author approves a version per scene with an explicit command or click: `inkforge approve --scene/--chapter/--all`. Bulk approve lists what will be approved and asks for confirmation.
- **Approval is never implied.** `--yes` (and "autopilot" in the UI) only answers *planning* gates (A–D). It never approves scene versions and never triggers an export. See [Approval & `--yes`](#approval--yes).
- **Preflight report:** missing/unapproved scenes, empty chapters, unresolved high/blocker findings, open plot promises, duplicate IDs, stale drafts, unsupported features for the format.
- **Final export** assembles approved versions in outline order. If anything is unapproved, preflight fails.
- **Draft preview export** (`inkforge export --preview`): assembles the *active* version of every scene, approved or not, into `exports/<ts>-preview/`. Every file is watermarked "DRAFT PREVIEW — contains unapproved text" and the preflight report lists each unapproved scene. A preview never counts as a release and isn't listed among final exports. This is how you read the output of an autopilot run.
- Exporters: Markdown (Phase 4), DOCX + EPUB (Phase 10), Fountain + PDF (Phase 9/10).
- Every export includes a note that human proofreading is required.

## Approval & `--yes`

Approval gates and approvals are separate things:

| Thing | What `--yes` / autopilot does | Audit record |
|---|---|---|
| Planning gates A–D (spec, design, outline, cards) | Auto-answers "approve" so the run continues | `actor: user, via: "--yes", job_id` — you typed the flag, so it's your decision, but it's marked as unattended |
| Codex/canon candidates (characters, items, facts) | **Not** accepted; left in the review queues | — |
| Scene version approval (H) | **Never** | — |
| Final export | **Never** (`--preview` export is allowed) | export event |

So `run --mode full --yes` takes an idea all the way to drafted, critiqued scenes and a draft preview. It stops there. Turning that into a final manuscript always takes an explicit `approve`, which is a deliberate action even when bulk-approving.

## Script mode

Added in Phase 9 via the `screenplay` / `tv-episode` / `stage-play` form profiles. The pipeline shape is identical; templates and checks change.

| Stage | Prose (novel) | Script (screenplay) |
|---|---|---|
| A Spec | POV, tense, target words | Logline, format (feature/TV), target pages, rating |
| B Design | Structure template | Beat sheet (Save the Cat / 8-sequence / TV act breaks), A/B/C storylines |
| C Units | Chapters | Sequences (feature) or acts with teaser/tag (TV) |
| D Cards | Scene cards | Scene cards + **slugline** (INT./EXT., location, DAY/NIGHT), page estimate |
| E Draft | Prose scene in Markdown | **Fountain** text: action lines, CHARACTER cues, parentheticals, dialogue, transitions |
| F Critique | Continuity, voice, craft | + format linter (slugline syntax, cue capitalization, action-line length, "camera directions" overuse), dialogue-to-action ratio, page timing |
| H Export | MD / DOCX / EPUB | Fountain / PDF (industry layout: Courier 12pt, margins) / FDX (Final Draft XML, stretch) |

Writing scripts as **Fountain** (plain-text screenplay markup) keeps them diffable, versionable and easy for a model to produce. Format correctness is enforced by a deterministic parser + linter, not by the model. A prose→script "adapt" stage (turn an approved novel chapter into scenes) is a stretch goal.

## Stage summary

| Stage | Profile | Artifact | Default gate | Phase |
|---|---|---|---|---|
| A Spec | planner | `spec/book_spec.vN.json` | on | 3 |
| B Design | planner | `design/story_design.vN.json` | on | 3 |
| C Units | planner | `outline/outline.vN.json` + `outline/chapters/*/plan.vN.json` | on | 3 |
| D Scene cards | planner | `scenes/*/card.vN.json` | off | 3 |
| E Draft | prose | `scenes/*/draft.vN.md` | off | 4 |
| F Critique | critic | `quality/findings.jsonl` | — | 6 |
| G Revise | prose + critic | `quality/revisions/*`, new draft | per diff | 6 |
| H Export | — (deterministic) | `exports/<ts>/…` | confirm | 4 (MD), 10 |
