# 09 — UI / UX

Two front doors onto the same engine: the **CLI** (Phases 1–6, always supported) and a **local web workbench** (Phases 7–8). Both go through the same pipeline/storage packages; the web app talks to the FastAPI service on `127.0.0.1`.

## Interaction rules

1. **AI proposes, author disposes.** Generated content arrives as a proposal (new version, diff, candidate fact). Accept/reject is always one click/keystroke away.
2. **Nothing silent.** Canon changes, overwrites, deletions and exports show what will change and ask for confirmation.
3. **Everything editable.** Any artifact can be edited by hand; editing creates a version with `created_by: user` and marks dependants stale.
4. **Always show the "why".** Every generation links to its context manifest and prompt; every finding links to its evidence.
5. **Progressive expansion** (OpenWrite): premise → acts → chapters → scenes, one level at a time, author can stop at any level.
6. **Keyboard-first outline** for authors who don't want a canvas.

## CLI

```bash
inkforge init "The Glass Orchard" --form novel            # creates project, opens spec wizard
inkforge spec  --project glass-orchard [--enhance]         # Stage A
inkforge design --project glass-orchard --structure three-act
inkforge outline --project glass-orchard [--act 2]         # Stage C
inkforge cards --project glass-orchard --chapter ch03      # Stage D
inkforge draft --project glass-orchard --scene ch03-sc02   # Stage E (or --chapter / --all)
inkforge check --project glass-orchard [--scene …]         # Stage F
inkforge revise --project glass-orchard --finding find-0031
inkforge approve --project glass-orchard --scene ch03-sc02 [--version 3]   # or --chapter / --all (lists + confirms)
inkforge export --project glass-orchard --format md|docx|epub|fountain|pdf [--preview]   # --preview = watermarked, unapproved allowed
inkforge run --project glass-orchard --mode full|plan|draft|polish [--until cards] [--yes]   # --yes answers planning gates A–D only
inkforge timeline --project glass-orchard [--place ch12-sc01 --after ch03-sc02]   # story-order view/edit
inkforge jobs / resume <job_id> / cancel <job_id>
inkforge facts review --project glass-orchard              # interactive review queue
inkforge status --project glass-orchard                     # every planning artifact: active/approved/fresh|review|stale
inkforge codex --project glass-orchard [--accept id|all] [--reject id]   # review pipeline-proposed candidates
inkforge edit --project glass-orchard chapter ch03         # $EDITOR → validated user version → staleness report
inkforge prompts [id] | doctor [--profile-set fast] | bench [--profile-set fast]
inkforge draft --project glass-orchard [--scene ch03-sc02 | --chapter ch03] [--redraft]   # Stage E
inkforge approve --project glass-orchard drafts --chapter ch03 [--confirm]   # lists, then asks
inkforge diff --project glass-orchard draft ch03-sc02 [--from 1 --to 2] | show … manifest ch03-sc02
inkforge findings --project glass-orchard [--scene …] [--dismiss find-0003] [--intentional …]
inkforge memory rebuild|consolidate
inkforge reindex | backup | restore <zip> | stats | doctor | bench
```

`inkforge doctor` checks runtime health, model availability, disk, workspace permissions, and offline config.

## Web workbench views

| View | What it does | Built on | Phase |
|---|---|---|---|
| **Projects** | List, create (form picker), progress vs. target, open | — | 7a |
| **Spec & Design** | Forms for Book Spec/Story Design, original vs. enhanced diff, approve | forms | 7a |
| **Outline** | Hierarchical tree (acts/chapters/scenes), drag-reorder with validation, inline edit, run stage on a node | dnd-kit | 7a |
| **Manuscript** | Editor per scene; version picker; diff view; inline finding markers; approve; word count + progress | Tiptap / CodeMirror (Fountain) | 7a |
| **Run Console** | Live job events (SSE), stage progress, tokens/s, context size, pause/resume/cancel/retry, link to manifest/prompt | SSE | 7a |
| **Quality Desk** | Findings table (filter by severity/category/scene), side-by-side evidence, accept/dismiss/intentional, launch revision, review diffs | — | 7b |
| **Fact Review** | Review queue for extracted facts with source quotes, bulk accept; Codex candidates (characters, items, locations); promise registry | — | 7b |
| **Story Map** | Graph of premise/acts/chapters/scenes/characters/locations/promises; filters; auto-layout; "expand this node" (generate children); "promote to manuscript" | React Flow + ELK | 8 |
| **Timeline** | Edits story (chronological) order in `timeline.json`, separate from narrative order; place flashbacks; mark concurrent scenes; character presence lanes; flags presence/travel conflicts | custom SVG / vis-timeline | 8 |
| **Codex** | Character/location/lore/item pages (items: holder + condition over story time), voice cards, relationships, linked appearances and facts | forms + links | 8 |
| **Settings** | Model profiles, health, approval gates, budgets, lessons, offline status | — | 7a |

## Story map semantics

Node types: `premise, act, chapter/sequence, scene, character, location, lore, item, promise(setup/payoff)`.
Edge types: `contains, precedes, depends-on, introduces, pays-off, appears-in, knows, located-at`.

The graph is a **view of the structured data**, not its own store. Dragging a scene between chapters is an outline edit (validated, journaled, undoable). Edges that represent derived data (appears-in) are read-only; author-authored edges (depends-on, pays-off) are stored on the entities.

## API shape (Phase 7)

REST resources mirror entities: `/projects`, `/projects/{id}/spec`, `/outline`, `/scenes/{id}`, `/scenes/{id}/versions`, `/codex/{kind}/{id}`, `/facts`, `/findings`, `/jobs`, `/exports`. Actions as POST sub-resources: `/scenes/{id}/draft`, `/findings/{id}/revise`, `/scenes/{id}/approve`. Events: `GET /events?project=` (SSE). Optimistic concurrency via `If-Match: <version hash>` → 409 on conflict (OpenWrite's concurrent-edit detection).

## Accessibility & polish

Keyboard navigation for outline, quality desk and review queue; visible focus; colour is never the only signal for severity; light/dark theme; autosave with clear saved/unsaved state.
