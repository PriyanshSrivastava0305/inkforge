# pyright: reportInvalidTypeForm=false, reportCallIssue=false, reportArgumentType=false
# (dynamic per-run schemas: Literal enums of the generated character ids)
"""Mini-book benchmark through the real router + prompt registry (Phase 0 harness, Phase 2 exit criterion).

spec → N chapters → cards per chapter → drafted scenes → evidence-first critique.
Writes per-call run records to bench-out/<date>-<model>/runs/<step>/ and a report to docs/benchmarks/.
Schemas here are deliberately small benchmark shapes; the stage contracts arrive in Phase 3.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, create_model

from inkforge.config import REPO_ROOT
from inkforge.models.recorder import RunRecorder
from inkforge.models.router import ModelRouter, StructuredOutputError
from inkforge.models.types import Generation
from inkforge.prompts.registry import PromptRegistry, RenderedPrompt
from inkforge.prompts.skills import SkillRegistry

FIXTURE = REPO_ROOT / "tests" / "golden" / "mini_book" / "fixture.yaml"
BENCH_V = 1  # benchmark prompts are pinned so results stay comparable as stage prompts evolve


# Every text field has a minimum length: under constrained decoding small models otherwise
# emit "" for fields they find hard (seen on llama3.2:1b, 2026-10-09).
Text = Annotated[str, Field(min_length=3)]


class Character(BaseModel):
    name: str = Field(min_length=2)
    role: Text
    want: Text
    voice: Text = Field(description="one line on how they talk")


class BookSpec(BaseModel):
    logline: str = Field(min_length=20)
    genre: Text
    tone: Text
    pov: Literal["first", "third-limited", "omniscient"]
    tense: Literal["past", "present"]
    themes: list[str] = Field(min_length=2, max_length=4)
    characters: list[Character] = Field(min_length=2, max_length=4)


class ChapterPlan(BaseModel):
    title: Text
    purpose: Text
    conflict: Text
    turning_point: Text
    outcome: Text


class Outline(BaseModel):
    chapters: list[ChapterPlan]


def outline_model(n: int) -> type[Outline]:
    return create_model(
        "Outline", __base__=Outline, chapters=(list[ChapterPlan], Field(min_length=n, max_length=n))
    )


class SceneCard(BaseModel):
    pov_character_id: str
    participants: list[str] = Field(min_length=1)
    location: Text
    want: Text
    obstacle: Text
    change: Text
    value_charge: str = Field(pattern=r"^[+-] *(->|→) *[+-]$")
    concrete_event: Text
    exit_condition: Text


class SceneCards(BaseModel):
    cards: list[SceneCard]


def cards_model(n: int, char_ids: list[str]) -> type[SceneCards]:
    """IDs as enums + bounded lists: constrained decoding then *cannot* emit a free-text name or loop
    through a list forever (llama3.2:1b did both, 2026-10-09). The allowed-ID check stays as a backstop."""
    char_id = Literal[tuple(char_ids)]  # type: ignore[valid-type]
    card = create_model(
        "SceneCard",
        __base__=SceneCard,
        pov_character_id=(char_id, ...),
        participants=(list[char_id], Field(min_length=1, max_length=len(char_ids))),
    )
    return create_model(
        "SceneCards", __base__=SceneCards, cards=(list[card], Field(min_length=n, max_length=n))
    )


class Finding(BaseModel):
    quote: str = Field(min_length=8, description="exact words copied from the scene")
    category: Literal["continuity", "voice", "pacing", "clarity", "prose", "motivation"]
    severity: Literal["low", "med", "high"]
    problem: str = Field(min_length=10, description="what is wrong, in your own words")
    suggestion: Text


class Critique(BaseModel):
    findings: list[Finding] = Field(min_length=1, max_length=5)


# ---------------------------------------------------------------- evidence matching (Phase 6 will reuse)

_FOLD = str.maketrans({c: "'" for c in '"“”‘’`´«»'} | {c: "-" for c in "–—‒―"})


def normalize_quote(s: str) -> str:
    """Fold quote marks and dashes: models swap " for ' when emitting JSON (seen 2026-10-09, 8B)."""
    return re.sub(r"\s+", " ", s.translate(_FOLD)).strip().lower()


def quote_found(quote: str, text: str, threshold: float = 0.95) -> bool:
    q, t = normalize_quote(quote), normalize_quote(text)
    if not q:
        return False
    if q in t:
        return True
    m = difflib.SequenceMatcher(None, t, q, autojunk=False).find_longest_match(0, len(t), 0, len(q))
    return m.size / len(q) >= threshold


def join_without_overlap(a: str, b: str, max_overlap: int = 400) -> str:
    """Append continuation b to a, removing the longest suffix-of-a / prefix-of-b overlap."""
    b2 = b.lstrip()
    for k in range(min(max_overlap, len(a), len(b2)), 15, -1):
        if a.endswith(b2[:k]):
            return a + b2[k:]
    return a + ("" if a.endswith((" ", "\n")) else " ") + b2


# ---------------------------------------------------------------- run


@dataclass
class StepStat:
    step: str
    ref: str
    attempts: int = 0
    valid: bool = False
    first_try: bool = False
    out_tokens: int = 0
    prompt_tokens: int = 0
    gen_tok_s: float = 0.0
    prompt_tok_s: float = 0.0
    wall_s: float = 0.0
    done_reason: str = ""
    errors: list[str] = field(default_factory=list)

    def add(self, g: Generation) -> None:
        self.out_tokens += g.output_tokens
        self.prompt_tokens += g.prompt_tokens
        self.gen_tok_s, self.prompt_tok_s = round(g.tokens_per_s, 2), round(g.prompt_tokens_per_s, 1)
        self.wall_s = round(self.wall_s + g.wall_s, 1)
        self.done_reason = g.finish_reason


class Bench:
    def __init__(self, router: ModelRouter, out: Path, *, profile_set: str | None):
        self.router, self.out, self.profile_set = router, out, profile_set
        self.prompts, self.skills = PromptRegistry(), SkillRegistry()
        self.stats: list[StepStat] = []

    def _rec(self, step: str, rp: RenderedPrompt) -> RunRecorder:
        rec = RunRecorder(self.out / "runs" / step)
        rec.prompt(
            rp.messages, prompt_ref=rp.ref, profile=rp.profile, model=self.router.profile(rp.profile).model
        )
        return rec

    async def structured(self, step: str, schema: type[BaseModel], rp: RenderedPrompt, **kw: Any) -> Any:
        st = StepStat(step, rp.ref)
        rec = self._rec(step, rp)
        try:
            res = await self.router.generate_structured(rp.messages, schema, rp.profile, recorder=rec, **kw)
            attempts, value = res.attempts, res.value
            st.valid = True
        except StructuredOutputError as e:
            attempts, value = e.attempts, None
        for a in attempts:
            st.add(a.generation)
            st.errors += a.errors
        st.attempts, st.first_try = len(attempts), st.valid and len(attempts) == 1
        rec.finish("valid" if st.valid else "failed")
        self.stats.append(st)
        print(
            f"  {step:<16} valid={st.valid} attempts={st.attempts} tok={st.out_tokens} {st.gen_tok_s} tok/s "
            f"prompt={st.prompt_tokens}@{st.prompt_tok_s}/s {st.wall_s}s",
            flush=True,
        )
        return value

    async def prose(
        self, step: str, rp: RenderedPrompt, original_user: str, target_words: int, max_cont: int = 2
    ) -> str:
        st = StepStat(step, rp.ref, valid=True, first_try=True)
        rec = self._rec(step, rp)
        messages, text = rp.messages, ""
        max_tokens = int(target_words * 1.33 * 1.25)
        for i in range(1 + max_cont):
            g = await self.router.generate(messages, rp.profile, max_tokens=max_tokens)
            rec.attempt(i + 1, g)
            st.add(g)
            st.attempts = i + 1
            text = g.text if i == 0 else join_without_overlap(text, g.text)
            if g.finish_reason != "length":
                break
            messages = self.prompts.render(
                "draft.continue", BENCH_V, original_request=original_user, anchor=text[-1200:]
            ).messages
        rec.finish("ok", words=len(text.split()))
        (self.out / f"{step}.md").write_text(text, encoding="utf-8")
        self.stats.append(st)
        print(
            f"  {step:<16} words={len(text.split())} calls={st.attempts} tok={st.out_tokens} {st.gen_tok_s} tok/s "
            f"prompt={st.prompt_tokens}@{st.prompt_tok_s}/s {st.wall_s}s",
            flush=True,
        )
        return text


async def run_bench(
    router: ModelRouter,
    *,
    profile_set: str | None = None,
    structure: str | None = "three-act",
    out_root: Path = REPO_ROOT / "bench-out",
    report_dir: Path = REPO_ROOT / "docs" / "benchmarks",
    fixture: Path = FIXTURE,
) -> Path:
    fx = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    model = router.profile("planner").model
    date, slug = time.strftime("%Y-%m-%d"), model.replace(":", "_").replace("/", "_")
    b = Bench(router, out_root / f"{date}-{slug}", profile_set=profile_set)
    t0 = time.time()
    print(f"== mini-book benchmark (router): {model} ==")

    spec = await b.structured(
        "spec",
        BookSpec,
        b.prompts.render(
            "spec.generate",
            BENCH_V,
            form=fx["form"],
            target_words=fx["target_words"],
            idea=fx["idea"].strip(),
        ),
    )
    report: dict[str, Any] = {"spec": spec}
    if spec is not None:
        char_ids = [f"char-{re.sub(r'[^a-z0-9]+', '-', c.name.lower()).strip('-')}" for c in spec.characters]
        cast = "\n".join(
            f"- {cid}: {c.name} ({c.role}); wants {c.want}; voice: {c.voice}"
            for cid, c in zip(char_ids, spec.characters, strict=True)
        )
        spec_block = (
            f"BOOK SPEC\nLogline: {spec.logline}\nGenre: {spec.genre}. Tone: {spec.tone}. "
            f"POV: {spec.pov}, {spec.tense} tense.\nThemes: {', '.join(spec.themes)}\nCAST\n{cast}\n"
        )
        skill = b.skills.get(structure) if structure else None
        outline = await b.structured(
            "outline",
            outline_model(fx["chapters"]),
            b.prompts.render(
                "outline.chapters", BENCH_V, spec_block=spec_block, structure=skill, chapters=fx["chapters"]
            ),
        )
        report["outline"] = outline
        cards_by_ch: list[list[SceneCard]] = []
        for i, ch in enumerate(outline.chapters if outline else [], 1):
            res = await b.structured(
                f"cards_ch{i}",
                cards_model(fx["scenes_per_chapter"], char_ids),
                b.prompts.render(
                    "cards.generate",
                    BENCH_V,
                    spec_block=spec_block,
                    number=i,
                    chapter=ch,
                    scenes=fx["scenes_per_chapter"],
                    char_ids=char_ids,
                ),
                allowed_ids={"character": char_ids},
            )
            cards_by_ch.append(res.cards if res else [])
        scenes: dict[tuple[int, int], str] = {}
        for ci, si in fx["draft_scenes"]:
            if len(cards_by_ch) < ci or len(cards_by_ch[ci - 1]) < si:
                continue
            card = cards_by_ch[ci - 1][si - 1]
            names = {cid: c.name for cid, c in zip(char_ids, spec.characters, strict=True)}
            rp = b.prompts.render(
                "draft.scene",
                BENCH_V,
                spec_block=spec_block,
                chapter_no=ci,
                scene_no=si,
                card=card,
                pov_name=names[card.pov_character_id],
                participants=[names.get(p, p) for p in card.participants],
                recap=scenes.get((ci, si - 1), "")[-1200:],
                target_words=fx["draft_target_words"],
                pov=spec.pov,
                tense=spec.tense,
            )
            user = next(m.content for m in rp.messages if m.role == "user")
            scenes[(ci, si)] = await b.prose(f"draft_ch{ci}_sc{si}", rp, user, fx["draft_target_words"])
        report["scenes"] = scenes
        ck = tuple(fx["critique_scene"])
        if ck in scenes:
            crit = await b.structured(
                "critique",
                Critique,
                b.prompts.render("critique.scene", BENCH_V, text=scenes[ck], max_findings=5),
            )
            if crit is not None:
                found = [quote_found(f.quote, scenes[ck]) for f in crit.findings]
                echo = [normalize_quote(f.problem) == normalize_quote(f.quote) for f in crit.findings]
                report["critique"] = (crit, found, echo)
                print(
                    f"  evidence found {sum(found)}/{len(found)}; problem repeats quote {sum(echo)}/{len(echo)}"
                )
    return _write_report(b, model, date, slug, time.time() - t0, report, report_dir, profile_set)


def _write_report(
    b: Bench,
    model: str,
    date: str,
    slug: str,
    total: float,
    rep: dict[str, Any],
    report_dir: Path,
    profile_set: str | None,
) -> Path:
    structured = [s for s in b.stats if not s.step.startswith("draft")]
    first, ok = sum(s.first_try for s in structured), sum(s.valid for s in structured)
    lines = [
        f"# Mini-book benchmark (router) — {model} — {date}",
        "",
        f"Profile set: `{profile_set or 'default'}` · prompts from the registry · run records in "
        f"`bench-out/{date}-{slug}/runs/` (git-ignored)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total wall time | {total / 60:.1f} min |",
        f"| Structured calls valid first try | {first}/{len(structured)} |",
        f"| Structured calls valid after repair | {ok}/{len(structured)} |",
    ]
    if "critique" in rep:
        _, found, echo = rep["critique"]
        lines += [
            f"| Critique quotes found in scene | {sum(found)}/{len(found)} |",
            f"| Critique `problem` merely repeats the quote | {sum(echo)}/{len(echo)} |",
        ]
    lines += [
        "",
        "| Step | Prompt | Valid | Attempts | Out tok | Gen tok/s | Prompt tok | Prompt tok/s | Wall s | Errors |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in b.stats:
        errs = "; ".join(s.errors)[:120].replace("|", "/")
        lines.append(
            f"| {s.step} | `{s.ref}` | {s.valid} | {s.attempts} | {s.out_tokens} | {s.gen_tok_s} | "
            f"{s.prompt_tokens} | {s.prompt_tok_s} | {s.wall_s} | {errs} |"
        )
    if spec := rep.get("spec"):
        lines += [
            "",
            f"**Logline:** {spec.logline}",
            "",
            "Cast: " + "; ".join(f"{c.name} ({c.role})" for c in spec.characters),
        ]
    if outline := rep.get("outline"):
        lines += ["", "## Outline", ""] + [
            f"{i}. **{c.title}** — {c.purpose}" for i, c in enumerate(outline.chapters, 1)
        ]
    if "critique" in rep:
        crit, found, _ = rep["critique"]
        lines += ["", "## Critique", ""] + [
            f"- [{f.severity}/{f.category}] “{f.quote}” — {f.problem} → {f.suggestion}{'' if ok_ else ' **(quote not found)**'}"
            for f, ok_ in zip(crit.findings, found, strict=True)
        ]
    for (ci, si), text in rep.get("scenes", {}).items():
        lines += [
            "",
            f"## Draft ch{ci} sc{si} ({len(text.split())} words)",
            "",
            text.strip(),
            "",
            "**Your rating (1–5):** _ · notes: _",
        ]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{date}-minibook-router-{slug}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {path}  ({total / 60:.1f} min)")
    return path
