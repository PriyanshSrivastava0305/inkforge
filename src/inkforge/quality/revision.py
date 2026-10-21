"""Stage G helpers (docs/07 § Stage G): where an edit may happen, proof it stayed there, and whether
it fixed what it was for. Pure functions — the stage (pipeline/stages/revise.py) does the model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from inkforge.domain.models import Finding
from inkforge.pipeline.text import word_diff
from inkforge.quality.extract import locate_quote

PASSES = ("continuity", "voice", "craft", "slop")  # specialist order for "polish" (docs/07)
NOT_LOCAL = {"rule:length", "rule:participants", "rule:canon_promise"}  # need the author, not a span edit
CONTINUITY_CATEGORIES = {"continuity", "knowledge", "timeline", "promise"}


def pass_of(f: Finding) -> str:
    if f.category in CONTINUITY_CATEGORIES or f.detector.startswith(("rule:canon_", "rule:name")):
        return "continuity"
    if f.category == "voice":
        return "voice"
    if f.detector in ("rule:slop", "rule:repetition"):
        return "slop"
    return "craft"


@dataclass
class Window:
    """A stretch of the scene (whole paragraphs) an edit may change, and the findings it serves."""

    start: int
    end: int
    findings: list[Finding] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)  # the evidence as it reads in the current text


def evidence_in(text: str, f: Finding) -> tuple[int, int] | None:
    """Where this finding's evidence for its own scene is in the *current* text (it may have moved)."""
    for ev in f.evidence:
        if ev.scene_id != f.scene_id:
            continue
        if text[ev.span[0] : ev.span[1]] == ev.quote:
            return ev.span
        found = locate_quote(text, ev.quote)
        if found is not None:
            return found[0], found[1]
    return None


def paragraph_bounds(text: str, span: tuple[int, int]) -> tuple[int, int]:
    """Expand a span to the whole paragraph(s) around it (blank-line separated)."""
    start = text.rfind("\n\n", 0, span[0])
    start = 0 if start < 0 else start + 2
    end = text.find("\n\n", span[1])
    return start, len(text) if end < 0 else end


def windows(text: str, findings: list[Finding]) -> tuple[list[Window], list[Finding]]:
    """Group findings into non-overlapping paragraph windows. Returns (windows, findings whose
    evidence is no longer in the text)."""
    placed: list[Window] = []
    lost: list[Finding] = []
    for f in findings:
        span = evidence_in(text, f)
        if span is None:
            lost.append(f)
            continue
        s, e = paragraph_bounds(text, span)
        w = Window(s, e, [f], [text[span[0] : span[1]]])
        for other in list(placed):  # merge overlapping or touching windows
            if w.start <= other.end and other.start <= w.end:
                placed.remove(other)
                w = Window(min(w.start, other.start), max(w.end, other.end), other.findings + w.findings,
                           other.quotes + w.quotes)  # fmt: skip
        placed.append(w)
    return sorted(placed, key=lambda w: w.start), lost


def changed_ranges(a: str, b: str) -> list[tuple[int, int]]:
    """Character ranges of `a` that differ in `b` (insertions are zero-width ranges at their position)."""
    out: list[tuple[int, int]] = []
    pos = 0
    for op in word_diff(a, b):
        if op.op == "equal":
            pos += len(op.text)
        elif op.op == "delete":
            out.append((pos, pos + len(op.text)))
            pos += len(op.text)
        elif not (out and out[-1][1] == pos):  # an insertion right after a deletion is one replacement
            out.append((pos, pos))
    return out


def outside(a: str, b: str, start: int, end: int) -> list[tuple[int, int]]:
    """Changes from `a` to `b` that fall outside [start, end] — must be empty for a span edit."""
    return [(s, e) for s, e in changed_ranges(a, b) if s < start or e > end]


def splice(text: str, w: Window, passage: str) -> str:
    return text[: w.start] + passage + text[w.end :]


_WRAPPER = re.compile(
    r"^\s*(?:here(?:'s| is) (?:the )?(?:revised|rewritten)[^\n]*:\s*\n|revised passage:\s*\n)", re.I
)


def clean_passage(raw: str) -> str:
    """Model output → just the passage: drop a lead-in line, wrapping quotes/fences and outer space."""
    t = _WRAPPER.sub("", raw.strip())
    t = re.sub(r"^```[a-z]*\n|\n```$", "", t.strip())
    if t.startswith('"""') and t.endswith('"""'):
        t = t[3:-3]
    return t.strip()


MIN_KEPT = 0.4  # share of the passage's content words an *edit* keeps (a replacement keeps ~none)
_FUNCTION = set(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "he",
        "she",
        "they",
        "her",
        "his",
        "him",
        "them",
        "their",
        "i",
        "you",
        "we",
        "me",
        "my",
        "our",
        "your",
        "not",
        "no",
        "so",
        "then",
        "there",
        "here",
        "had",
        "has",
        "have",
        "did",
        "do",
        "does",
    ]
)


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2 and w not in _FUNCTION}


def shape_problem(old: str, new: str, names: list[str] | None = None) -> str | None:
    """Reasons a rewrite can't be a local edit of `old` (before any checks run)."""
    if not new.strip():
        return "the rewrite was empty"
    ratio = len(new.split()) / max(1, len(old.split()))
    if ratio < 0.5 or ratio > 1.8:
        return f"the rewrite is {ratio:.1f}× the length of the passage; change only what the issue needs"
    if new.count("\n\n") > old.count("\n\n") + 1:
        return "the rewrite added paragraphs; keep the passage's paragraph breaks"
    before = _content(old)
    if len(before) >= 5 and len(before & _content(new)) / len(before) < MIN_KEPT:
        return "the rewrite replaced the passage instead of editing it; keep its events and wording"
    for name in names or []:
        if re.search(rf"\b{re.escape(name)}\b", old) and not re.search(rf"\b{re.escape(name)}\b", new):
            return f"the rewrite dropped {name} from the passage"
    return None
