"""Mini-book benchmark (Phase 0): spec -> 3 chapters -> 6 cards -> 2 drafts -> 1 critique.

    uv run python scripts/bench_models.py llama3.2:1b
    uv run python scripts/bench_models.py llama3.1:8b-instruct-q4_K_M      # ~1 h on CPU; run in background

Per call it records: schema-valid on first try, attempts used (repair loop), tokens, tok/s,
prompt tokens and prompt-read speed (shows prefix-cache reuse), wall time.
The critique step also measures evidence validity: do quoted spans exist verbatim in the scene?

Writes raw outputs to bench-out/<date>-<model>/ and a report to docs/benchmarks/<date>-minibook-<model>.md.
Prompts are inline on purpose — the prompt registry arrives in Phase 2.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field, ValidationError, create_model

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "golden" / "mini_book" / "fixture.yaml"
OLLAMA = "http://127.0.0.1:11434"
ALLOWED = {"llama3.1:8b-instruct-q4_K_M", "llama3.2:1b"}
SYSTEM = "You are a careful story-planning assistant. Follow the output contract exactly."


# ---------- schemas (subset of docs/04; full domain models come in Phase 1) ----------


class Character(BaseModel):
    name: str = Field(min_length=2)
    role: str
    want: str
    voice: str = Field(description="one line on how they talk")


class BookSpec(BaseModel):
    logline: str = Field(min_length=20)
    genre: str
    tone: str
    pov: str = Field(pattern="^(first|third-limited|omniscient)$")
    tense: str = Field(pattern="^(past|present)$")
    themes: list[str] = Field(min_length=2, max_length=4)
    characters: list[Character] = Field(min_length=2, max_length=4)


class ChapterPlan(BaseModel):
    title: str
    purpose: str
    conflict: str
    turning_point: str
    outcome: str


class Outline(BaseModel):
    chapters: list[ChapterPlan]


def outline_model(n: int) -> type[Outline]:
    """Chapter count is part of the schema, so a wrong count fails validation and triggers repair."""
    return create_model(
        "Outline", __base__=Outline, chapters=(list[ChapterPlan], Field(min_length=n, max_length=n))
    )


def scene_cards_model(char_ids: list[str], n: int) -> type[BaseModel]:
    """Cards whose POV/participants must come from the allowed id set (D6)."""
    from typing import Literal

    CharId = Literal[tuple(char_ids)]  # type: ignore[valid-type]
    Card = create_model(
        "SceneCard",
        pov_character_id=(CharId, ...),
        participants=(list[CharId], Field(min_length=1)),
        location=(str, ...),
        want=(str, ...),
        obstacle=(str, ...),
        change=(str, ...),
        value_charge=(str, Field(pattern=r"^[+-] *(->|→) *[+-]$")),
        concrete_event=(str, ...),
        exit_condition=(str, ...),
    )
    return create_model("SceneCards", cards=(list[Card], Field(min_length=n, max_length=n)))  # type: ignore[valid-type]


class Finding(BaseModel):
    category: str = Field(pattern="^(continuity|voice|pacing|clarity|prose|motivation)$")
    severity: str = Field(pattern="^(low|med|high)$")
    claim: str
    quote: str = Field(min_length=8, description="exact words copied from the scene")


class Critique(BaseModel):
    findings: list[Finding] = Field(min_length=1, max_length=5)


# ---------- runtime ----------


@dataclass
class CallStat:
    step: str
    attempts: int = 0
    valid_first_try: bool | None = None
    valid: bool = False
    out_tokens: int = 0
    gen_tok_s: float = 0.0
    prompt_tokens: int = 0
    prompt_tok_s: float = 0.0
    done_reason: str = ""
    wall_s: float = 0.0
    errors: list[str] = field(default_factory=list)


class Bench:
    def __init__(self, model: str, seed: int, out: Path):
        self.model, self.seed, self.out = model, seed, out
        self.client = httpx.Client(base_url=OLLAMA, timeout=1800)
        self.stats: list[CallStat] = []

    def _chat(
        self,
        messages: list[dict],
        *,
        fmt: Any = None,
        temperature: float,
        num_predict: int,
        num_ctx: int = 6144,
    ) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "num_predict": num_predict,
                "seed": self.seed,
                "top_p": 0.92,
                "repeat_penalty": 1.08,
            },
        }
        if fmt is not None:
            body["format"] = fmt
        r = self.client.post("/api/chat", json=body)
        r.raise_for_status()
        return r.json()

    def _record(self, st: CallStat, d: dict, wall: float) -> None:
        ev, ed = d.get("eval_count", 0), d.get("eval_duration", 0) / 1e9
        pe, pd = d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 0) / 1e9
        st.out_tokens += ev
        st.gen_tok_s = round(ev / ed, 2) if ed else 0.0
        st.prompt_tokens += pe
        st.prompt_tok_s = round(pe / pd, 1) if pd else 0.0
        st.done_reason = d.get("done_reason", "")
        st.wall_s = round(st.wall_s + wall, 1)

    def structured(
        self,
        step: str,
        schema: type[BaseModel],
        user: str,
        *,
        temperature: float = 0.4,
        num_predict: int = 1200,
        max_attempts: int = 3,
    ) -> BaseModel | None:
        """Constrained decoding + Pydantic validation + repair retry + temperature backoff (docs/08)."""
        st = CallStat(step)
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        result = None
        for attempt in range(1, max_attempts + 1):
            st.attempts = attempt
            temp = temperature if attempt < 3 else max(0.1, temperature / 2)
            t0 = time.time()
            d = self._chat(
                messages, fmt=schema.model_json_schema(), temperature=temp, num_predict=num_predict
            )
            self._record(st, d, time.time() - t0)
            raw = d["message"]["content"]
            (self.out / f"{step}.a{attempt}.json").write_text(raw, encoding="utf-8")
            try:
                result = schema.model_validate_json(raw)
                st.valid = True
            except ValidationError as e:
                err = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
                st.errors.append(err)
                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"That output failed validation: {err}. Return the corrected JSON only.",
                    },
                ]
            if st.valid_first_try is None:
                st.valid_first_try = st.valid
            if st.valid:
                break
        self.stats.append(st)
        print(
            f"  {step:<16} valid={st.valid} attempts={st.attempts} tok={st.out_tokens} "
            f"{st.gen_tok_s} tok/s prompt={st.prompt_tokens}@{st.prompt_tok_s}/s {st.wall_s}s",
            flush=True,
        )
        return result

    def prose(self, step: str, user: str, *, target_words: int, max_continuations: int = 2) -> str:
        st = CallStat(step, valid_first_try=True, valid=True)
        messages = [
            {
                "role": "system",
                "content": "You are a novelist. Write only scene prose: no titles, "
                "headings, notes or commentary.",
            },
            {"role": "user", "content": user},
        ]
        text = ""
        num_predict = int(target_words * 1.33 * 1.25)  # ~1.33 tokens/word for English prose, +25% headroom
        for i in range(1 + max_continuations):
            st.attempts = i + 1
            t0 = time.time()
            d = self._chat(messages, temperature=0.85, num_predict=num_predict)
            self._record(st, d, time.time() - t0)
            chunk = d["message"]["content"]
            text = (text + chunk) if i == 0 else _join_without_overlap(text, chunk)
            if d.get("done_reason") != "length":
                break
            anchor = text[-1200:]
            messages = [
                messages[0],
                {
                    "role": "user",
                    "content": f'{user}\n\nThe scene so far ends with:\n"""{anchor}"""\n\n'
                    "Continue directly from the last word. Do not repeat any earlier text. "
                    "Finish the scene.",
                },
            ]
        (self.out / f"{step}.md").write_text(text, encoding="utf-8")
        self.stats.append(st)
        print(
            f"  {step:<16} words={len(text.split())} calls={st.attempts} tok={st.out_tokens} "
            f"{st.gen_tok_s} tok/s prompt={st.prompt_tokens}@{st.prompt_tok_s}/s {st.wall_s}s",
            flush=True,
        )
        return text


def _join_without_overlap(a: str, b: str, max_overlap: int = 400) -> str:
    """Append continuation b to a, removing the longest suffix-of-a / prefix-of-b overlap."""
    b_stripped = b.lstrip()
    for k in range(min(max_overlap, len(a), len(b_stripped)), 15, -1):
        if a.endswith(b_stripped[:k]):
            return a + b_stripped[k:]
    sep = "" if a.endswith((" ", "\n")) else " "
    return a + sep + b_stripped


_QUOTES = str.maketrans({c: "'" for c in '"“”‘’`´«»'} | {c: "-" for c in "–—‒―"})


def _norm(s: str) -> str:
    """Fold quote marks and dashes: models swap " for ' when emitting JSON (seen 2026-10-09, 8B)."""
    return re.sub(r"\s+", " ", s.translate(_QUOTES)).strip().lower()


def quote_found(quote: str, text: str, threshold: float = 0.95) -> bool:
    q, t = _norm(quote), _norm(text)
    if q in t:
        return True
    sm = difflib.SequenceMatcher(None, t, q, autojunk=False)
    m = sm.find_longest_match(0, len(t), 0, len(q))
    return len(q) > 0 and m.size / len(q) >= threshold


# ---------- pipeline ----------


def run(model: str) -> Path:
    if model not in ALLOWED:
        raise SystemExit(f"model {model!r} not allowed (D16): {sorted(ALLOWED)}")
    fx = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    date = time.strftime("%Y-%m-%d")
    slug = model.replace(":", "_").replace("/", "_")
    out = ROOT / "bench-out" / f"{date}-{slug}"
    out.mkdir(parents=True, exist_ok=True)
    b = Bench(model, fx["seed"], out)
    t_start = time.time()
    print(f"== mini-book benchmark: {model} ==")

    spec = b.structured(
        "spec",
        BookSpec,
        f"Create a book spec for a {fx['form']} (~{fx['target_words']} words) from this idea:\n"
        f"{fx['idea']}\nGive every character a proper personal name.",
    )
    if spec is None:
        return _report(b, model, date, slug, t_start, aborted="spec failed")
    assert isinstance(spec, BookSpec)
    char_ids = [f"char-{re.sub(r'[^a-z0-9]+', '-', c.name.lower()).strip('-')}" for c in spec.characters]
    cast = "\n".join(
        f"- {cid}: {c.name} ({c.role}); wants {c.want}; voice: {c.voice}"
        for cid, c in zip(char_ids, spec.characters, strict=True)
    )
    spec_block = (
        f"BOOK SPEC\nLogline: {spec.logline}\nGenre: {spec.genre}. Tone: {spec.tone}. "
        f"POV: {spec.pov}, {spec.tense} tense.\nThemes: {', '.join(spec.themes)}\nCAST\n{cast}\n"
    )

    outline = b.structured(
        "outline",
        outline_model(fx["chapters"]),
        f"{spec_block}\nPlan exactly {fx['chapters']} chapters. Each chapter must cause the "
        "next; the last resolves the dramatic question.",
    )
    if outline is None:
        return _report(b, model, date, slug, t_start, aborted="outline failed")
    assert isinstance(outline, Outline)
    chapters = outline.chapters

    cards_by_ch: list[list[Any]] = []
    CardsModel = scene_cards_model(char_ids, fx["scenes_per_chapter"])
    for i, ch in enumerate(chapters, 1):
        res = b.structured(
            f"cards_ch{i}",
            CardsModel,
            f"{spec_block}\nCHAPTER {i}: {ch.title}\nPurpose: {ch.purpose}\nConflict: {ch.conflict}\n"
            f"Turning point: {ch.turning_point}\nOutcome: {ch.outcome}\n\n"
            f"Split this chapter into exactly {fx['scenes_per_chapter']} scene cards. Use only these "
            f"character ids: {', '.join(char_ids)}. value_charge looks like '+ -> -'.",
        )
        cards_by_ch.append(res.cards if res is not None else [])  # type: ignore[attr-defined]

    scenes: dict[tuple[int, int], str] = {}
    for ci, si in fx["draft_scenes"]:
        cards = cards_by_ch[ci - 1]
        if len(cards) < si:
            continue
        card = cards[si - 1]
        pov = spec.characters[char_ids.index(card.pov_character_id)]
        prev = scenes.get((ci, si - 1), "")
        recap = f'\nPREVIOUS SCENE ENDED:\n"""{prev[-1200:]}"""\n' if prev else ""
        scenes[(ci, si)] = b.prose(
            f"draft_ch{ci}_sc{si}",
            f"{spec_block}\nSCENE CARD (chapter {ci}, scene {si})\nPOV: {pov.name}\n"
            f"Participants: {', '.join(card.participants)}\nLocation: {card.location}\nWant: {card.want}\n"
            f"Obstacle: {card.obstacle}\nEvent: {card.concrete_event}\nChange: {card.change}\n"
            f"Exit when: {card.exit_condition}\n{recap}\nWrite this scene in about {fx['draft_target_words']} words, "
            f"{spec.pov} POV, {spec.tense} tense. Use the characters' names, not their ids.",
            target_words=fx["draft_target_words"],
        )

    evidence_valid = None
    ck = tuple(fx["critique_scene"])
    if ck in scenes:
        text = scenes[ck]
        crit = b.structured(
            "critique",
            Critique,
            f'SCENE TEXT\n"""{text}"""\n\nList up to 5 concrete weaknesses in this scene. '
            "For each, copy the exact words from the scene into 'quote' (at least 8 characters, "
            "copied verbatim).",
            temperature=0.1,
        )
        if crit is not None:
            assert isinstance(crit, Critique)
            hits = [quote_found(f.quote, text) for f in crit.findings]
            evidence_valid = (sum(hits), len(hits))
            (out / "critique.checked.json").write_text(
                json.dumps(
                    [{**f.model_dump(), "quote_found": h} for f, h in zip(crit.findings, hits, strict=True)],
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print(f"  evidence quotes found verbatim: {evidence_valid[0]}/{evidence_valid[1]}")

    return _report(
        b,
        model,
        date,
        slug,
        t_start,
        spec=spec,
        chapters=chapters,
        scenes=scenes,
        evidence_valid=evidence_valid,
    )


def _report(
    b: Bench,
    model: str,
    date: str,
    slug: str,
    t_start: float,
    *,
    aborted: str | None = None,
    spec: BookSpec | None = None,
    chapters: list[ChapterPlan] | None = None,
    scenes: dict | None = None,
    evidence_valid: tuple[int, int] | None = None,
) -> Path:
    total = time.time() - t_start
    structured = [s for s in b.stats if not s.step.startswith("draft")]
    first = sum(1 for s in structured if s.valid_first_try)
    eventual = sum(1 for s in structured if s.valid)
    (b.out / "stats.json").write_text(json.dumps([asdict(s) for s in b.stats], indent=2), encoding="utf-8")

    lines = [
        f"# Mini-book benchmark — {model} — {date}",
        "",
        "Fixture: `tests/golden/mini_book/fixture.yaml` · script: `scripts/bench_models.py` · "
        f"raw outputs: `bench-out/{date}-{slug}/` (git-ignored)",
        "",
    ]
    if aborted:
        lines += [f"**ABORTED:** {aborted}", ""]
    lines += [
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total wall time | {total / 60:.1f} min |",
        f"| Structured calls schema-valid first try | {first}/{len(structured)} |",
        f"| Structured calls valid after repair | {eventual}/{len(structured)} |",
        f"| Evidence quotes found verbatim (critique) | "
        f"{f'{evidence_valid[0]}/{evidence_valid[1]}' if evidence_valid else 'n/a'} |",
        "",
        "## Calls",
        "",
        "| Step | Valid | Attempts | Out tok | Gen tok/s | Prompt tok | Prompt tok/s | "
        "done_reason | Wall s | Errors |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in b.stats:
        errs = "; ".join(s.errors)[:120].replace("|", "/")
        lines.append(
            f"| {s.step} | {s.valid} | {s.attempts} | {s.out_tokens} | {s.gen_tok_s} | {s.prompt_tokens} | "
            f"{s.prompt_tok_s} | {s.done_reason} | {s.wall_s} | {errs} |"
        )
    if spec:
        lines += [
            "",
            "## Spec",
            "",
            f"**Logline:** {spec.logline}",
            "",
            "Cast: " + "; ".join(f"{c.name} ({c.role})" for c in spec.characters),
        ]
    if chapters:
        lines += ["", "## Outline", ""] + [
            f"{i}. **{c.title}** — {c.purpose}" for i, c in enumerate(chapters, 1)
        ]
    for (ci, si), text in (scenes or {}).items():
        lines += [
            "",
            f"## Draft ch{ci} sc{si} ({len(text.split())} words)",
            "",
            text.strip(),
            "",
            "**Your rating (1–5):** _ · notes: _",
        ]
    report = ROOT / "docs" / "benchmarks" / f"{date}-minibook-{slug}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {report.relative_to(ROOT)}  ({total / 60:.1f} min)")
    return report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    run(sys.argv[1])
