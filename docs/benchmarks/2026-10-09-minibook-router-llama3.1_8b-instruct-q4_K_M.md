# Mini-book benchmark (router) — llama3.1:8b-instruct-q4_K_M — 2026-10-09

Profile set: `default` · prompts from the registry · run records in `bench-out/2026-10-09-llama3.1_8b-instruct-q4_K_M/runs/` (git-ignored)

| Metric | Value |
|---|---|
| Total wall time | 22.9 min |
| Structured calls valid first try | 6/6 |
| Structured calls valid after repair | 6/6 |
| Critique quotes found in scene | 5/5 |
| Critique `problem` merely repeats the quote | 0/5 |

| Step | Prompt | Valid | Attempts | Out tok | Gen tok/s | Prompt tok | Prompt tok/s | Wall s | Errors |
|---|---|---|---|---|---|---|---|---|---|
| spec | `spec.generate@1` | True | 1 | 260 | 3.19 | 106 | 10.0 | 109.2 |  |
| outline | `outline.chapters@1` | True | 1 | 298 | 3.02 | 526 | 11.3 | 145.3 |  |
| cards_ch1 | `cards.generate@1` | True | 1 | 348 | 2.97 | 353 | 29.3 | 129.4 |  |
| cards_ch2 | `cards.generate@1` | True | 1 | 317 | 2.98 | 367 | 29.5 | 119.0 |  |
| cards_ch3 | `cards.generate@1` | True | 1 | 378 | 3.03 | 363 | 27.8 | 137.8 |  |
| draft_ch1_sc1 | `draft.scene@1` | True | 1 | 558 | 2.94 | 375 | 11.6 | 222.2 |  |
| draft_ch1_sc2 | `draft.scene@1` | True | 1 | 654 | 2.72 | 677 | 17.0 | 279.9 |  |
| critique | `critique.scene@1` | True | 1 | 432 | 2.65 | 764 | 10.9 | 233.1 |  |

**Logline:** A lighthouse keeper discovers a mysterious series of letters in bottles that predict storms, but the letters are written in her own handwriting, forcing her to confront the darkness of her own past.

Cast: Astrid Jensen (protagonist); Ethan Blackwood (love interest, friend); Lena Grant (mysterious figure)

## Outline

1. **Chapter 1: The Lighthouse Keeper** — Setup (≈0–25% of the story)
2. **Chapter 2: The Storm Within** — Confrontation (≈25–50% of the story)
3. **Chapter 3: The Truth Revealed** — Resolution (≈75–100% of the story)

## Critique

- [low/prose] “the weight of it all settling back onto me” — The phrase is vague and doesn't provide a clear sense of what 'it all' refers to. → Replace with a specific detail, e.g. 'the weight of my grandmother's vase settling back onto me'.
- [low/prose] “Storms. Always the storms.” — The phrase feels like a cliché and doesn't add much depth to the story. → Replace with a more specific and personal reference to the storms, e.g. 'The storms that chased me away from home'.
- [low/prose] “What am I doing here? Why can't I remember this?” — The questions feel like a convenient plot device and don't ring true to the character's voice. → Replace with more specific and character-driven questions, e.g. 'What am I running from? Why did I leave my old life behind?'
- [low/prose] “Maybe I can do this – maybe I can start again” — The phrase feels like a cop-out and doesn't provide a clear sense of the character's agency. → Replace with a more specific and concrete goal, e.g. 'Maybe I can learn to love this place'.
- [high/prose] “where the sea and the lighthouse stand sentinel against the storms that come from within” — The phrase is overly sentimental and feels like a contrived way to tie the story together. → Replace with a more specific and nuanced description of the character's inner turmoil, e.g. 'where the sea and the lighthouse stand witness to the storms that rage within me'.

## Draft ch1 sc1 (458 words)

I stand at the edge of my bed, staring down at the stacks of boxes and crates scattered across the room. My new life as a lighthouse keeper is supposed to be a fresh start, a chance to leave the chaos of my past behind. But as I sort through the familiar objects – a vase that was my grandmother's, a worn cookbook from my childhood – I feel the weight of it all settling back onto me.

I try to focus on the task at hand: unpacking and organizing my new space. The lighthouse is a labyrinth of narrow staircases and cramped rooms, but my quarters are small and cozy. The window frames the lighthouse beam in a warm golden light, casting a reassuring glow over everything.

As I fold a stack of towels, my fingers brush against something hidden among them. A piece of paper, crumpled and creased. I smooth it out, recognizing the familiar handwriting as my own. A shiver runs down my spine. Where did this come from? I must have written it... but when?

I scan the page quickly – a rough sketch of the lighthouse's interior, annotated with cryptic notes and symbols. The lines seem to blur together as I stare at them, but one phrase stands out: "the storms are coming."

Storms. Always the storms.

I fold the paper back up, trying to shake off the unease creeping over me. It's just a note from my old life, leftover clutter that I can safely toss in the trash. But as I look around the room, I catch sight of other objects with the same handwriting: a recipe card stuck between two books on the shelf, a rough map of the coastline pinned to the wall.

My mind starts racing, questions tumbling out like waves: What am I doing here? Why can't I remember this?

A knock at the door breaks the spell, and I turn to see Ethan standing in the doorway, a friendly smile on his face. "Hey, Astrid – just checking in. How's it going?"

I force a smile, feeling a twinge of gratitude for his interruption. "Good. Just... settling in." I hold up the crumpled paper, trying to appear nonchalant. "Just found this old note from myself."

Ethan's eyes scan the page quickly before he takes it from me. "Looks like you were planning some... creative renovations," he says with a chuckle.

I watch as he hands the paper back to me, feeling a sense of calm wash over me in the face of his gentle reassurance. Maybe I can do this – maybe I can start again, here on this rocky coast, where the sea and the lighthouse stand sentinel against the storms that come from within.

**Your rating (1–5):** _ · notes: _

## Draft ch1 sc2 (545 words)

I stand at the top of the lighthouse's spiral staircase, my footfalls echoing off the metal walls as I make my way to the lantern room. The smell of old oil and saltwater fills my lungs, a familiar scent that should bring me comfort but instead makes me feel like I'm trapped in a never-ending loop. Ethan is already there, his back to me as he examines the instruments on the control panel.

I approach him quietly, not wanting to startle him, but he must have sensed my presence because he turns and smiles when I clear my throat to get his attention. "Hey," I say, trying to sound normal despite the knot in my stomach.

Ethan's smile is warm and reassuring, a gentle thing that makes me want to lean into it, to let its comfort seep into my bones. But I push that thought away, focusing on the task at hand. We need to check the equipment, make sure everything is in working order. It's not just a matter of routine maintenance – it's also about my own competence. Can I do this? Am I capable?

Ethan notices my hesitation and holds out a reassuring hand. "Want me to go over it with you?" he asks, his voice calm and steady.

I nod, feeling a flutter in my chest as I let him take the lead. We move through the equipment checklist together, Ethan explaining each item as we check it off. I listen intently, trying to absorb the information, but my mind keeps wandering back to those letters – the ones I wrote, or maybe someone else did? It's impossible to shake the feeling that something is off, that there's a thread of truth hidden just beneath the surface.

As we work, our conversation turns to small talk. Ethan mentions his plans for the weekend – a fishing trip with some friends – and I try to contribute, to seem like I'm part of this world, but my words feel forced, stilted. We reach the end of the list without incident, but as I turn to pack up our things, Ethan catches my arm.

"Astrid?" he says, his voice low and gentle. "You okay? You look a little... distracted."

I force a smile, feeling a twinge of gratitude towards him for noticing, for caring. It's been weeks since we met – weeks since I arrived on this rocky coast with nothing but a suitcase full of questions and a faint memory of a life left behind. Weeks since Ethan Blackwood walked into my life and started to... well, I'm not quite sure what he's doing yet, but it feels like something.

As we make our way down the stairs, I feel the weight of this new life settle onto me. It's a crushing thing, sometimes – the isolation, the responsibility, the feeling that I'm just pretending to be someone I'm not. But with Ethan by my side, even for just a few hours, it feels... manageable.

I glance over at him as we reach the ground floor, and our eyes meet in a moment of quiet understanding. He's watching me with a mixture of concern and affection, and I feel my heart stutter in response. Maybe I'm not alone here after all.

**Your rating (1–5):** _ · notes: _
