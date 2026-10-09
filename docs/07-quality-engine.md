# 07 — Quality Engine

Covers Stage F (critique) and Stage G (revise). Principle: **rules first, model second, author decides.** A finding without evidence is either a hypothesis (labelled) or it isn't shown.

## Pipeline per scene

```
approved-or-draft scene text
   │
   ├─▶ 1. Deterministic checks ──────────────┐
   ├─▶ 2. Fact extraction → review queue      │
   ├─▶ 3. Canon conflict checks (rules) ──────┤──▶ findings.jsonl ──▶ Quality Desk
   ├─▶ 4. Model adjudication (ambiguous only) ┤          │
   └─▶ 5. Model critics (voice, craft) ───────┘          ▼
                                              accepted findings → Stage G revise
```

## 1. Deterministic checks (cheap, run always)

| Check | Detects |
|---|---|
| Schema & card conformance | Missing participants, POV character absent, location never mentioned |
| Name/alias consistency | Misspelled names (edit distance to Codex names/aliases), unknown capitalized names |
| Repetition | Repeated n-grams within scene and across last K scenes; overused words; same sentence openers |
| "Slop" lint | List of cliché/AI-tell phrases (configurable per genre), em-dash density, "suddenly", hedges — style warnings only |
| Meta leakage | "As an AI", headings, prompt echoes, bracketed instructions |
| POV / tense drift | Tense ratio per paragraph; first-person "I" in third-person book, etc. |
| Length | Out of tolerance vs. card target |
| Script format (script mode) | Fountain parse errors, slugline syntax, cue capitalization, overlong action blocks |

## 2. Fact extraction (review queue)

- After a scene is drafted/approved, a planner-profile call extracts **candidate facts** as structured triples with the exact supporting quote: `(subject_id, predicate, value, quote)`.
- Code verifies the quote exists verbatim in the text (fuzzy ≥ 0.95) — otherwise the fact is discarded (prevents hallucinated evidence).
- Subject must resolve to a known Codex id (`char-*`, `loc-*`, `obj-*`, `lore-*`) or a proposed new entity (which goes into the Codex candidate queue along with the fact).
- Predicates come from a controlled vocabulary to keep checks deterministic: `age, eye_color, hair, height, status (alive/dead/injured), injury, location_at, holds_object, knows_fact, relationship, title_rank, date_of_event, rule_of_world` for characters/world, and `held_by, located_at, condition (intact/broken/lost/destroyed)` for items. Free-form predicates are allowed but only checked by the model.
- `valid_from` is the scene being extracted from. The previous value of a single-valued, time-varying predicate (e.g. `held_by`) gets `valid_to` set automatically when the new fact is accepted.
- Candidates go to `canon/review_queue.jsonl`. Author accepts (→ canon), rejects, or bulk-accepts low-risk types. Setting: auto-accept extracted facts that don't conflict with existing canon (off by default).

## 3. Canon conflict rules

All temporal comparisons use **story order** (`story_index` from the Timeline, [04 § Story time](04-data-model.md#story-time)), never narrative order. A flashback in chapter 12 that happens before chapter 3 is checked as if it sat before chapter 3.

| Rule | Example | Needs |
|---|---|---|
| Single-valued predicate conflict | eyes grey (ch02) vs. blue (ch09) | facts only |
| Temporal validity | dead in ch10, speaks in ch12 | story order |
| Knowledge constraint | Character references fact F before the scene where they learned F | story order |
| Presence | Character in two locations at the same story time | `concurrent_with` or equal `story_datetime` |
| Object state | Item held by A, then used by B with no transfer; destroyed item reappears | Item facts + story order |
| Age/date arithmetic | Ages vs. dates vs. elapsed story time | absolute `story_datetime` on both scenes (skipped otherwise) |
| Travel plausibility (opt-in) | Distance / time between locations below configured speed | absolute datetimes + location distances |
| Promise tracking | Promise open at the end with no payoff; payoff without setup | promise registry |

**`temporal_mode` relaxations** (from the SceneCard):

| Mode | Effect |
|---|---|
| `linear` | All rules apply |
| `flashback` / `flashforward` | Rules apply at the scene's Timeline position. Requires explicit placement; if it's missing, a `timeline` finding is raised instead of the content checks |
| `dream` | Facts from the scene are not extracted into canon; single-valued, presence and object-state rules skipped; knowledge rule still applies (a character can't dream about what they never learned, unless marked prophetic in notes) |
| `unreliable` | Facts extracted with `confidence ≤ 0.5` and never auto-accepted; conflicts become `is_hypothesis` findings at low severity |

Every rule produces a finding that cites **both** passages (or the passage + the canon fact's source/author action). Non-linear scenes are handled by the `temporal_mode` table above.

## 4. Model adjudication

Used only when rules can't decide (free-form predicates, paraphrased conflicts, "is 'the old soldier' the same as char-bram?"). The critic gets both passages and the fact, returns `{conflict: yes/no/unclear, explanation, confidence}`. `unclear` → finding marked `is_hypothesis`.

## 5. Model critics

All return `Finding[]` with evidence spans and are **advisory**.

- **Voice critics (per character)** — AuthorAgent's persona idea. For each major character with dialogue in the scene: give the critic the voice card + 3–5 approved sample lines and only *that character's* lines. Asks: does this sound like them? would they know/say this? is the motivation consistent? Returns flagged lines and optional in-voice rewrites.
- **Craft rubric** — one call scoring 1–5 with justification on: scene goal achieved (card's `change` happened), tension, clarity, pacing, sensory grounding, dialogue quality, show-vs-tell, ending hook. Scores are used by the keep-if-better loop and trend charts; low scores become findings only with quoted evidence.
- **Reader panel (later, optional)** — 3–5 reader personas give quick reactions (bored here / confused here / loved this). Phase 6 stretch or post-v1.

Critic outputs are validated: evidence quotes must exist in the text, otherwise the finding is dropped.

## Finding lifecycle

```
open ──▶ accepted ──▶ (revision) ──▶ fixed
  │        └──────▶ rejected-by-author
  ├──▶ dismissed
  └──▶ intentional   (also suppresses the same rule for that pair of passages)
```

Severity defaults: canon conflicts = high, knowledge/timeline = high, voice = medium, craft = low, slop lint = low. Blockers are configurable (e.g., unresolved high continuity blocks export preflight).

## Stage G — revision loop

```
for finding_group in accepted_findings grouped by scene and locality:
    instruction = render("revise_scoped", finding, evidence, scene, card, voice cards)
    candidate   = prose_model(instruction)          # span-level when possible, full scene if needed
    diff        = word_diff(base, candidate)
    reject if diff touches text outside allowed spans (for span edits)
    rerun: rules that fired + fact extraction on changed spans + craft rubric
    keep if: target finding resolved AND no new high findings AND rubric_total >= base - ε
    else retry (≤ max_attempts) with failure reason; then give up and mark "needs author"
    store RevisionProposal(diff, scores_before, scores_after, decision)
```

Specialist passes (AuthorAgent's revision orchestrator) run in this order when the author picks "Polish chapter": continuity → voice → craft → slop. Each pass only fixes its own category so diffs stay small and attributable.

## Learning loop (lessons)

When the same finding category recurs (e.g., ≥ 3 times in 10 scenes), the author can promote it to a **lesson**: a short line added to the drafting prompt's style rules ("Mira never swears", "avoid opening scenes with weather"). Lessons are stored in the project, versioned, and visible — never added silently.

## Evaluation

- `tests/golden/continuity_book/`: a 30-scene fixture with 25 planted contradictions of each rule type and 10 decoys (intentional/flashback). Track recall, precision, false-positive rate per rule.
- Critic calibration: author-labelled set of 50 findings to measure precision of model critics per model profile.
