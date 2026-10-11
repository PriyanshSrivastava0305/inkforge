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
    r"\b(?:as an ai|language model|i cannot (?:write|continue)|\[insert|\{\{|word count:)", re.IGNORECASE
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
