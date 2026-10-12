# Sample project: *Salt Letters*

A short story planned, drafted and exported by InkForge with **no human edits**, kept as an honest sample of what the pipeline produces today.

| | |
|---|---|
| Produced | 2026-10-12, by `tests/integration/test_offline_acceptance.py` (`INKFORGE_OFFLINE_FIXTURE=short_story INKFORGE_OFFLINE_PROFILE=default`) |
| Model | `llama3.1:8b-instruct-q4_K_M` via Ollama 0.34, CPU only (i7-1165G7, 16 GB) |
| Run | `run --mode full --yes` → `approve drafts --all` → `export`; 72 min; 11 model calls, all valid first try |
| Network | socket guard active: zero non-loopback connection attempts |
| Length | 2204 words in 3 scenes (target was 3,600) |

## The idea given

> A lighthouse keeper on a drowned coast finds letters in bottles that predict storms a day before they happen. The letters are in her own handwriting.

## What the planner made of it

**Logline:** A lighthouse keeper discovers a mysterious connection to her own past when she finds letters in bottles predicting storms, forcing her to confront the truth about her life and her connection to the sea.

**Dramatic question:** Will the protagonist uncover the truth about her past and her connection to the sea, or will she be consumed by the secrets she uncovers?

- **Setup the Protagonist's Ordinary World and Introduce the Inciting Incidnt** — Introduce the protagonist's ordinary world, establish her connection to the sea, and set the stage for the inciting incident.
- **The Search for Answers** — Escalate the obstacles and challenges for the protagonist as she tries to uncover the truth about the letters and her past
- **The Truth Revealed** — Resolve the protagonist's journey and answer the dramatic question.

Cast (Codex candidates, never auto-accepted): Emilia Grey (protagonist), James Reed (supporting)

## Known weaknesses in this sample

- **Too short:** scenes came out at 635–865 words against a 1,200-word target. Since this run, a scene that ends under 60% of its target gets one extension pass.
- **Draft checks that were wrong at the time** (fixed since): the first-person narrator was reported as "never named", and the contraction "I'd" as an unknown name.
- **No long-book memory yet:** each scene only saw the previous scene's ending (Phase 5 adds digests, established facts and recalled passages).
- **Not proofread.** Read it as raw model output.

The manuscript is in [`book.md`](book.md); the export's preflight report is [`preflight.json`](preflight.json).
