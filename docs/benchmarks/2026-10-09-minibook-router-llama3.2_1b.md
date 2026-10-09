# Mini-book benchmark (router) — llama3.2:1b — 2026-10-09

Profile set: `fast` · prompts from the registry · run records in `bench-out/2026-10-09-llama3.2_1b/runs/` (git-ignored)

| Metric | Value |
|---|---|
| Total wall time | 5.8 min |
| Structured calls valid first try | 6/6 |
| Structured calls valid after repair | 6/6 |
| Critique quotes found in scene | 5/5 |
| Critique `problem` merely repeats the quote | 0/5 |

| Step | Prompt | Valid | Attempts | Out tok | Gen tok/s | Prompt tok | Prompt tok/s | Wall s | Errors |
|---|---|---|---|---|---|---|---|---|---|
| spec | `spec.generate@1` | True | 1 | 347 | 11.18 | 116 | 1058.0 | 31.2 |  |
| outline | `outline.chapters@1` | True | 1 | 421 | 10.91 | 535 | 51.6 | 49.0 |  |
| cards_ch1 | `cards.generate@1` | True | 1 | 286 | 10.04 | 408 | 113.5 | 32.1 |  |
| cards_ch2 | `cards.generate@1` | True | 1 | 266 | 10.57 | 442 | 110.8 | 29.2 |  |
| cards_ch3 | `cards.generate@1` | True | 1 | 211 | 10.46 | 404 | 120.5 | 23.6 |  |
| draft_ch1_sc1 | `draft.scene@1` | True | 1 | 581 | 10.7 | 356 | 52.8 | 61.1 |  |
| draft_ch1_sc2 | `draft.scene@1` | True | 1 | 475 | 10.72 | 613 | 85.0 | 51.6 |  |
| critique | `critique.scene@1` | True | 1 | 576 | 10.61 | 797 | 48.8 | 70.7 |  |

**Logline:** A Lighthouse Keeper's Gift Novella Spec (24000 words, 1st edition, 2025-2035)

Cast: Ava Moreno (Lighthouse Keeper); Ethan Blackwood (Coast Guard Officer); Dr. Sophia Patel (Local Historian); Liam Chen (Storm Chaser)

## Outline

1. **Chapter 1: The Lighthouse Keeper's Secret** — Setup
2. **Chapter 2: The Coast Guard's Investigation** — Confrontation
3. **Chapter 3: The Storm Chaser's Gambit** — Resolution

## Critique

- [high/prose] “The creak of the wooden floorboards beneath her feet was a familiar rhythm to Ava's ears, a soothing serenade after weeks of solitude.” — The phrase 'a familiar rhythm' is a bit generic and doesn't add much to the scene. → Instead, describe the sound more specifically, e.g. the creak of the wooden floorboards beneath her feet was a soothing serenade after weeks of solitude, the sound echoing through the empty rooms.
- [low/prose] “She moved silently through the rooms, her eyes scanning every inch of the lighthouse keeper's residence.” — The phrase 'her eyes scanning every inch of the lighthouse keeper's residence' is a bit vague and doesn't reveal much about Ava's character. → Instead, describe Ava's actions more specifically, e.g. she moved silently through the rooms, her eyes scanning every inch of the lighthouse keeper's residence, her gaze lingering on the dusty shelves and faded photographs.
- [high/prose] “The air inside was stale and musty, but it was home, a place where she could escape the world outside.” — The phrase 'the air inside was stale and musty' is a bit clichéd and doesn't add much to the scene. → Instead, describe the air more specifically, e.g. the air inside was stale and musty, heavy with the scent of old books and decay.
- [low/prose] “As she entered her own small bedroom, a faint scent wafted out, a perfume she'd picked up on her last visitor.” — The phrase 'a faint scent wafted out' is a bit vague and doesn't reveal much about the scene. → Instead, describe the scent more specifically, e.g. the faint scent wafted out, a perfume she'd picked up on her last visitor, the sweet aroma of lavender and rose petals.
- [low/prose] “The thought sent a flutter through her chest, but Ava pushed it aside.” — The phrase 'the thought sent a flutter through her chest' is a bit generic and doesn't add much to the scene. → Instead, describe Ava's emotions more specifically, e.g. the thought sent a flutter through her chest, a mix of excitement and trepidation.

## Draft ch1 sc1 (481 words)

The creak of the wooden floorboards beneath her feet was a familiar rhythm to Ava's ears, a soothing serenade after weeks of solitude. She moved silently through the rooms, her eyes scanning every inch of the lighthouse keeper's residence. The air inside was stale and musty, but it was home, a place where she could escape the world outside.

As she entered her own small bedroom, a faint scent wafted out, a perfume she'd picked up on her last visitor. She wrinkled her nose, not wanting to disturb her solitude, but curiosity got the better of her. She lifted the drawer containing her clothes and began to search through her belongings. A note caught her eye, tucked away in a corner of her dresser.

The words were simple, yet tantalizing: "Find us at sea." Ava's heart skipped a beat as she recognized the handwriting – hers. The initials "A.M." danced on the page, a childish scribble that now seemed sinister.

She folded the note and tucked it into her pocket, a shiver running down her spine. Who was "us"? And what did they want from her? The thought sent a flutter through her chest, but Ava pushed it aside. She had to focus on her duties as lighthouse keeper. Besides, she wasn't exactly in need of visitors.

The hours ticked by, and the sun began to set outside, casting long shadows across the room. Ava's thoughts grew restless, her mind wandering to the coast guard station and the man who seemed to be searching for her. Ethan Blackwood – he was a good man, kind and fair. She remembered their brief conversation, his analytical gaze piercing through her defenses.

Just as she was starting to drift off into a light doze, Ava's eyes landed on a stack of letters on her nightstand. Her fingers trembled as she pulled them down, the familiar weight of the envelopes bringing back memories of past storms. She had always been drawn to the coastline during these times, when the wind and waves seemed to hold secrets.

As she began to sort through the letters, Ava's thoughts turned to the strange occurrences in town – whispers of a mysterious storm chaser who seemed to appear out of nowhere. Some said he was looking for something, but others claimed he was just chasing the thrill.

Ava's eyes narrowed, her mind racing with possibilities. She knew nothing about this man, Ethan Blackwood, or his connection to the letters. But she was determined to uncover the truth behind these cryptic messages, no matter what secrets they might hold.

The sound of footsteps outside her door broke the silence, and Ava's heart skipped a beat as she froze, her eyes locked on the crack in the doorway. She knew it was Ethan Blackwood, come to ask questions or perhaps – just possibly – share some information about the mysterious letters.

**Your rating (1–5):** _ · notes: _

## Draft ch1 sc2 (395 words)

The dim light of dawn seeped through the porthole above the station's kitchenette, casting an ethereal glow on the worn Formica countertops. Ava Moreno stood beside the sink, her eyes fixed on the stack of letters on the nightstand. The envelopes were tied with a faded ribbon, and the words on the first page seemed to shimmer in the morning air.

Just as she was starting to drift off into a light doze, her gaze piercing through her defenses.

Ava's thoughts turned to the strange occurrences in town – whispers of a mysterious storm chaser who seemed to appear out of nowhere. Some said he was looking for something, but others claimed he was just chasing the thrill.

Her fingers trembled as she pulled the stack down, the familiar weight of the envelopes bringing back memories of past storms. She had always been drawn to the coastline during these times, when the wind and waves seemed to hold secrets.

As she began to sort through the letters, Ava's thoughts turned to the strange occurrences in town – whispers of a mysterious storm chaser who seemed to appear out of nowhere. Some said he was looking for something, but others claimed he was just chasing the thrill.

Ava's eyes narrowed, her mind racing with possibilities. She knew nothing about this man, Ethan Blackwood, or his connection to the letters. But she was determined to uncover the truth behind these cryptic messages, no matter what secrets they might hold.

The sound of footsteps outside her door broke the silence, and Ava's heart skipped a beat as she froze, her eyes locked on the crack in the doorway. She knew it was Ethan Blackwood, come to ask questions or perhaps – just possibly – share some information about the mysterious letters.

Ava's mind flashed with memories of Ethan Blackwood – the way he smiled when he talked about storms, the way his eyes crinkled at the corners as he shared stories about his own experiences. She had thought she had pushed those feelings aside, but now they stirred in her chest like a low-grade hum.

"Can I help you?" Ava called out, trying to sound brusque despite her growing unease.

Ethan pushed open the door and stepped inside, his eyes scanning the kitchenette as if searching for something. "Just getting some coffee," he said, not meeting Ava's gaze.

**Your rating (1–5):** _ · notes: _
