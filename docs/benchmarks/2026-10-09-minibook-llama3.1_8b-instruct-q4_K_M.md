# Mini-book benchmark — llama3.1:8b-instruct-q4_K_M — 2026-10-09

Fixture: `tests/golden/mini_book/fixture.yaml` · script: `scripts/bench_models.py` · raw outputs: `bench-out/2026-10-09-llama3.1_8b-instruct-q4_K_M/` (git-ignored)

## Summary

| Metric | Value |
|---|---|
| Total wall time | 23.3 min |
| Structured calls schema-valid first try | 6/6 |
| Structured calls valid after repair | 6/6 |
| Evidence quotes found verbatim (critique) | 5/5 (after folding quote marks; claim merely repeats the quote in 5/5) |

## Calls

| Step | Valid | Attempts | Out tok | Gen tok/s | Prompt tok | Prompt tok/s | done_reason | Wall s | Errors |
|---|---|---|---|---|---|---|---|---|---|
| spec | True | 1 | 229 | 2.96 | 84 | 10.5 | stop | 103.7 |  |
| outline | True | 1 | 426 | 3.05 | 232 | 12.4 | stop | 158.6 |  |
| cards_ch1 | True | 1 | 414 | 2.79 | 391 | 22.2 | stop | 166.2 |  |
| cards_ch2 | True | 1 | 387 | 3.13 | 385 | 24.3 | stop | 139.6 |  |
| cards_ch3 | True | 1 | 278 | 3.17 | 383 | 23.5 | stop | 104.2 |  |
| draft_ch1_sc1 | True | 1 | 565 | 3.01 | 397 | 11.6 | stop | 222.3 |  |
| draft_ch1_sc2 | True | 1 | 607 | 2.81 | 699 | 16.1 | stop | 259.5 |  |
| critique | True | 1 | 517 | 2.74 | 635 | 11.0 | stop | 246.4 |  |

## Spec

**Logline:** When lighthouse keeper Ailsa MacLeod discovers letters in bottles predicting devastating storms, she must unravel the mystery of her own past to prevent catastrophe and uncover the truth about her own identity.

Cast: Ailsa MacLeod (protagonist); Ewen Douglas (supporting character); Elspeth MacLeod (antagonist)

## Outline

1. **Chapter 1: The Storm Within** — Introduction to Ailsa's world, her mysterious past, and the ominous predictions.
2. **Chapter 2: The Weight of Memory** — Ailsa delves deeper into her family's history and the secrets they've kept hidden.
3. **Chapter 3: The Eye of the Storm** — The mystery is solved, and Ailsa uncovers the truth about her past and the predictions.

## Draft ch1 sc1 (437 words)

I trudged up the worn stone steps to the lighthouse, the salty sea air stinging my face as I gazed out at the gray expanse of ocean. The storm that had rolled in last night still lingered, its aftermath a somber reminder that I was a long way from civilization. Ewen's words echoed in my mind: "You're a ghost on these shores, Ailsa." But I'd never felt more connected to this place than I did now.

As keeper of the lighthouse, it was my job to tend the light and watch for ships, but lately, something had been eating at me. Maybe it was the isolation, or maybe it was just the weather – whatever, I'd started finding these...letters. In bottles, on the beach, tossed out to sea like confetti from some unknown party. The first one I'd found a few weeks ago had sent a shiver down my spine. "Devastating storm approaches, 7th January". That was today. Not that I believed in this sort of thing – I mean, who writes letters in bottles and just sends them off into the void? But...

I spotted something dark on the sand as I walked along the shoreline. A bottle, half-buried, its contents spilling out onto the pebbles. My heart quickened as I picked it up, blowing away the grit to reveal a folded letter inside. The address was mine – but there was no return address, just my name on the envelope in neat, black script.

Ailsa MacLeod

I felt like I'd been punched in the gut. How did this get here? And what did it mean? My mind spun through possibilities: had someone sent this to me on purpose? But who? And why now?

As I unfolded the letter, a faint scent of lavender wafted up, transporting me back to some long-forgotten moment. The words were cryptic – "The tempest comes, and with it, you'll know" – but something about them resonated deep within me. Was it just my imagination playing tricks, or did this letter hold a connection to...something?

My hands shook as I refolded the letter and tucked it into my pocket. The storms had been getting worse, more frequent, like something was building on the horizon. Ewen would say it was just weather – but I knew better. This felt personal.

I took a deep breath of the salty air, trying to calm myself down, but the unease lingered. Elspeth's words echoed in my mind: "You don't need to know, Ailsa. Some secrets are best left buried." But I was starting to suspect that some secrets were exactly what I needed to uncover.

**Your rating (1–5):** _ · notes: _

## Draft ch1 sc2 (456 words)

I stepped into Ewen's office, a sense of trepidation gripping my chest. The historical society's rooms were always dimly lit, but today they seemed even darker, as if the shadows themselves were trying to swallow me whole. I'd met Ewen a few times before, at village gatherings and town meetings, but never in this capacity – as a seeker of truth.

"Ah, Ailsa," he said, looking up from his desk with a hint of surprise. "I didn't expect you so soon."

"I got the letter today," I replied, my voice barely above a whisper.

Ewen's expression turned serious. "Come in, come in. Let's talk about it."

He led me to a small table in front of a bookshelf, where a stack of yellowed papers and dusty files waited. The air was thick with the scent of old books and decay. I took a seat across from him, fidgeting with my hands.

Ewen pushed his chair back and leaned against it. "Tell me what's been going on."

I pulled out the letter, holding it out as if offering it to him like a fragile gift. The crease where I'd folded it was starting to unfold, revealing the cryptic message within: "The tempest comes, and with it, you'll know." Ewen's eyes scanned the words, his expression unreadable.

"Where did you find this?" he asked finally.

I hesitated, unsure how much to reveal. Could I trust him? But something about his calm demeanor put me at ease. "It was in a bottle, on the beach."

Ewen nodded thoughtfully. "I think we can work with this." He began sorting through the papers on the table, pulling out an old logbook and flipping through its yellowed pages.

"This is from 1850," he said, pointing to a handwritten entry. "A storm swept through these islands, causing widespread damage. The keeper at the time reported seeing...apparitions of some sort."

I raised an eyebrow. Apparitions?

"It sounds like something out of a storybook," Ewen said dryly, but his eyes were fixed intently on me.

"Maybe it's not just a storybook," I suggested, feeling a spark of hope ignite within me. "Maybe there's truth to it."

Ewen's gaze met mine, and for a moment, we just stared at each other. Then he nodded, a small smile playing on his lips. "I think we're onto something here."

A sense of calm washed over me as I realized I wasn't alone in this. Ewen was willing to help me dig into the mystery of my past, and that gave me a glimmer of hope. We were both quiet for a moment, lost in thought.

"Let's get to work," Ewen said finally, pushing his chair back from the table. "I think it's time we started uncovering some secrets."

**Your rating (1–5):** _ · notes: _
