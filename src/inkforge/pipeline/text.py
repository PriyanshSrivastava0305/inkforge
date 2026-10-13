"""Deterministic prose utilities for drafting: continuation joins, meta-text cleanup, word diffs."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Literal

_END = re.compile(r"""[.!?…]["'”’)\]]*\s*$""")


def word_count(text: str) -> int:
    return len(text.split())


def ends_mid_sentence(text: str) -> bool:
    """True when the text doesn't end on terminal punctuation (optionally followed by a closing quote)."""
    t = text.rstrip()
    return bool(t) and not _END.search(t)


def join_without_overlap(a: str, b: str, max_overlap: int = 600, min_overlap: int = 16) -> str:
    """Append continuation `b` to `a`, removing the longest suffix-of-a == prefix-of-b overlap.

    Models asked to "continue from the last word" often repeat the tail of the anchor first.
    """
    b2 = b.lstrip()
    for k in range(min(max_overlap, len(a), len(b2)), min_overlap - 1, -1):
        if a.endswith(b2[:k]):
            return a + b2[k:]
    # also catch a repeated last sentence with different leading whitespace
    tail = a.rstrip()[-200:]
    idx = b2.find(tail[-min(len(tail), 80) :]) if len(tail) >= min_overlap else -1
    if 0 <= idx < 300:
        b2 = b2[idx + min(len(tail), 80) :]
    sep = "" if a.endswith((" ", "\n")) or b2.startswith(("\n", " ")) else " "
    return a + sep + b2.lstrip(" ")


# ---------------------------------------------------------------- meta text


_META_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("heading", re.compile(r"^\s*#{1,6}\s+.*$", re.MULTILINE)),
    # only genuine labels: "Chapter 3", "Chapter 3: Title", "Scene 2 - The Bottle" (not "Chapter 3 was the inn.")
    (
        "label",
        re.compile(
            r"^\s*\**\s*(?:scene|chapter|part)\s+[\w-]+(?:\s*[:.\-–—]\s*[^.!?\n]{0,60})?\s*\**\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "preamble",
        re.compile(
            r"^\s*(?:here(?:'s| is) (?:the|a|your) (?:scene|draft|story)|sure[,!]|certainly[,!]).*$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    ("note", re.compile(r"^\s*(?:note|author'?s note|word count)\s*:.*$", re.IGNORECASE | re.MULTILINE)),
    ("rule", re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)),
]
_TIMESTAMP = re.compile(
    r"\s*\[?\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\]?\s*|\s*\[?\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\]?\s*",
    re.IGNORECASE,
)
_LEAK = re.compile(
    r"\b(?:as an ai|language model|i cannot (?:write|continue)|\[insert|\{\{|word count:)"
    # context section headers that must never survive into prose (backstop for strip_prompt_echo)
    r"|THE STORY SO FAR|EARLIER PASSAGES|REFERENCE NOTES|SCENE CARD|ESTABLISHED FACTS|THE PREVIOUS SCENE ENDED",
    re.IGNORECASE,
)


@dataclass
class Cleaned:
    text: str
    removed: list[tuple[str, str]]  # (kind, line)


def clean_scene_text(text: str) -> Cleaned:
    """Strip meta lines models add around prose (headings, "Here is the scene:", notes, rules).

    Only whole lines at the start or end of the text are removed: a "Chapter" line in the middle could
    be legitimate prose, so it is left for the meta-leakage check to flag instead.
    """
    lines = text.replace("\r\n", "\n").strip("\n").split("\n")
    removed: list[tuple[str, str]] = []

    def is_meta(line: str) -> str | None:
        for kind, pat in _META_PATTERNS:
            if pat.fullmatch(line):
                return kind
        return None

    for direction in (0, -1):
        while lines:
            line = lines[direction]
            if not line.strip():
                lines.pop(direction)
                continue
            kind = is_meta(line)
            if kind is None:
                break
            removed.append((kind, line.strip()))
            lines.pop(direction)
    kept = []
    for line in lines:  # a line that is only a date/time stamp is never prose (seen on llama3.2:1b)
        if _TIMESTAMP.fullmatch(line):
            removed.append(("timestamp", line.strip()))
        else:
            kept.append(line)
    body = "\n".join(kept)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return Cleaned(body, removed)


def meta_leaks(text: str) -> list[tuple[int, str]]:
    """Positions of model/meta artifacts that survived cleaning (for findings with evidence)."""
    return [(m.start(), text[max(0, m.start() - 20) : m.end() + 20]) for m in _LEAK.finditer(text)]


# ---------------------------------------------------------------- prompt echo

_CONTEXT_LABEL = re.compile(
    r"^\s*(?:THE STORY SO FAR|EARLIER PASSAGES[^\n]*|REFERENCE NOTES[^\n]*|THE PREVIOUS SCENE ENDED:?|SCENE CARD|BOOK SPEC|"
    r"ESTABLISHED FACTS[^\n]*|CHARACTERS IN THIS SCENE|CHAPTER:[^\n]*|(?:Purpose|Conflict|Outcome|POV|Present|Place|When|Want|"
    r"Obstacle|What happens|Turn|Change|Mood|Beats|End the scene when|Logline|Premise|Genre|Audience|Setting|Themes|"
    r"Avoid|Reader promises|Style):[^\n]*|(?:act\d+|ch\d{2,3}(?:-sc\d{2,3})?):[^\n]*|\[ch\d{2,3}-sc\d{2,3}\][^\n]*|\"{3})\s*$"
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_prompt_echo(text: str, prompt: str, *, min_chars: int = 40) -> tuple[str, list[str]]:
    """Remove prompt material the model copied into its output (llama3.2:1b, 2026-10-12: a third of a
    scene was the context — digests, the scene card and whole recalled passages — repeated verbatim).

    Dropped: any line of >= `min_chars` characters that occurs verbatim in the prompt, wherever it
    appears, and context labels/headers ("THE STORY SO FAR", "Purpose: ...", "ch01: ...") in the echoed
    block at the top. Short lines are kept unless they are labels, so ordinary dialogue that happens to
    match a few words survives.
    """
    haystack = _squash(prompt)
    prompt_lines = {_squash(ln) for ln in prompt.replace("\r\n", "\n").split("\n") if ln.strip()}
    lines = text.replace("\r\n", "\n").split("\n")
    removed: list[str] = []
    out: list[str] = []
    in_head = True  # until the first line of genuine prose
    for line in lines:
        bare = _squash(line)
        if not bare:
            out.append(line)
            continue
        probe = bare.strip("\"“”' ")
        if len(probe) >= min_chars and probe in haystack:
            removed.append(bare)
            continue
        # in the echoed block at the top, short copies count too: labels and whole prompt lines
        # (numbered beats, "POV: …"); after real prose starts, short lines are left alone
        if in_head and (_CONTEXT_LABEL.match(line) or bare in prompt_lines):
            removed.append(bare)
            continue
        in_head = False
        out.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return cleaned, removed


def near_copies(
    text: str, sources: list[str], *, threshold: float = 0.8, min_chars: int = 80
) -> list[tuple[str, float]]:
    """Paragraphs of `text` that closely paraphrase a sentence run from `sources` (recalled passages,
    recap). Small models reuse the wording of what they were shown; verbatim copies are stripped, these
    near-copies are reported for the author. Returns (paragraph opening, similarity)."""
    sentences = [
        x for src in sources for x in re.split(r"(?<=[.!?])\s+", _squash(src)) if len(x) >= min_chars // 2
    ]
    hits: list[tuple[str, float]] = []
    for para in (p_ for p_ in re.split(r"\n\s*\n", text) if len(p_.strip()) >= min_chars):
        first = re.split(r"(?<=[.!?])\s+", _squash(para))[0]
        if len(first) < min_chars // 2:
            continue
        best = max(
            (
                difflib.SequenceMatcher(None, first.lower(), x.lower(), autojunk=False).ratio()
                for x in sentences
            ),
            default=0.0,
        )
        if best >= threshold:
            hits.append((first[:90], round(best, 2)))
    return hits


# ---------------------------------------------------------------- word diff


@dataclass(frozen=True)
class DiffOp:
    op: Literal["equal", "insert", "delete"]
    text: str


def word_diff(a: str, b: str) -> list[DiffOp]:
    """Word-level diff that keeps whitespace attached, so ops re-join into the exact texts."""
    ta, tb = re.findall(r"\S+|\s+", a), re.findall(r"\S+|\s+", b)
    out: list[DiffOp] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(DiffOp("equal", "".join(ta[i1:i2])))
        else:
            if i2 > i1:
                out.append(DiffOp("delete", "".join(ta[i1:i2])))
            if j2 > j1:
                out.append(DiffOp("insert", "".join(tb[j1:j2])))
    return out


def diff_stats(ops: list[DiffOp]) -> dict[str, int]:
    return {k: sum(len(o.text.split()) for o in ops if o.op == k) for k in ("equal", "insert", "delete")}
