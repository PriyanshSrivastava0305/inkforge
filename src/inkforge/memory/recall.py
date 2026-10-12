"""RECALL tier (docs/06): exact passages from earlier scenes, retrieved on demand.

- Chunks: paragraphs of each scene's effective version (D34), merged/split to ~400–1,200 characters,
  stored with scene id, version and character offsets so a passage can always be cited.
- Index: `<project>/recall.sqlite` (FTS5, BM25). A cache: `sync()` re-chunks only scenes whose
  effective text changed; deleting the file loses nothing.
- Queries are built from the scene card without a model call: names and aliases of the characters,
  location and items present, plus keywords from promises the scene touches. BM25 down-weights
  names that appear everywhere, so rarer terms (an object, a place) decide the ranking.
- Only scenes the reader has already seen (earlier in narrative order) are searched.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from inkforge.domain.models import SceneCard
from inkforge.memory.effective import effective_text, scenes_before
from inkforge.storage.workspace import ProjectStore

MIN_CHARS, MAX_CHARS, HARD_MAX = 400, 1200, 1600
_PARA = re.compile(r"\n\s*\n")
_SENT_END = re.compile(r"(?<=[.!?…])[\"'”’)]*\s+")
_STOP = set(
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
        "for",
        "with",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "them",
        "his",
        "her",
        "their",
        "there",
        "then",
        "than",
        "when",
        "where",
        "what",
        "who",
        "whom",
        "which",
        "while",
        "will",
        "would",
        "could",
        "should",
        "have",
        "has",
        "had",
        "not",
        "no",
        "yes",
        "into",
        "onto",
        "over",
        "under",
        "after",
        "before",
        "again",
        "about",
        "must",
        "will",
        "that",
        "must",
        "their",
    ]
)

DDL = """
CREATE TABLE scenes (scene_id TEXT PRIMARY KEY, version INT, sha TEXT);
CREATE VIRTUAL TABLE chunks USING fts5(scene_id UNINDEXED, version UNINDEXED, start UNINDEXED, end UNINDEXED,
                                       text, tokenize='unicode61 remove_diacritics 2');
"""


@dataclass(frozen=True)
class Passage:
    scene_id: str
    version: int
    start: int
    end: int
    text: str
    score: float

    @property
    def ref(self) -> str:
        return f"draft:{self.scene_id}@v{self.version}#{self.start}-{self.end}"


def chunk_text(text: str) -> list[tuple[int, int, str]]:
    """(start, end, chunk) with `text[start:end] == chunk`. Paragraphs are merged up to MAX_CHARS and
    split at sentence ends when a single paragraph exceeds HARD_MAX."""
    paras: list[tuple[int, int]] = []
    pos = 0
    for m in _PARA.finditer(text):
        if text[pos : m.start()].strip():
            paras.append((pos, m.start()))
        pos = m.end()
    if text[pos:].strip():
        paras.append((pos, len(text)))
    pieces: list[tuple[int, int]] = []
    for a, b in paras:
        if b - a <= HARD_MAX:
            pieces.append((a, b))
            continue
        cur = a
        for m in _SENT_END.finditer(text, a, b):
            if m.end() - cur >= MAX_CHARS:
                pieces.append((cur, m.start()))
                cur = m.end()
        if b - cur > 0:
            pieces.append((cur, b))
    chunks: list[tuple[int, int]] = []
    for a, b in pieces:
        if chunks and (chunks[-1][1] - chunks[-1][0]) < MIN_CHARS and (b - chunks[-1][0]) <= MAX_CHARS:
            chunks[-1] = (chunks[-1][0], b)
        else:
            chunks.append((a, b))
    out = []
    for a, b in chunks:
        a2 = a + (len(text[a:b]) - len(text[a:b].lstrip()))
        b2 = b - (len(text[a:b]) - len(text[a:b].rstrip()))
        if b2 > a2:
            out.append((a2, b2, text[a2:b2]))
    return out


def keywords(text: str, limit: int = 6) -> list[str]:
    """Content words worth searching for (longest first; names keep their case-insensitive form)."""
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ'’-]{4,}", text) if w.lower() not in _STOP]
    seen: dict[str, None] = {}
    for w in sorted(words, key=len, reverse=True):
        seen.setdefault(w.lower().strip("'’-"), None)
    return list(seen)[:limit]


def card_terms(store: ProjectStore, card: SceneCard) -> list[str]:
    terms: list[str] = []
    for eid in [*card.participants, card.location_id, *card.items_present]:
        e = store.get_codex(eid) if eid else None
        if e is None:
            continue
        for name in (e.name, *e.aliases):
            terms += [t for t in re.findall(r"[A-Za-zÀ-ÿ'’-]{3,}", name) if t.lower() not in _STOP]
    touched = set(card.promises_touched)
    for p in store.get_promises().promises:
        if p.id in touched:
            terms += keywords(p.description, 4)
    for fid in card.required_facts:
        f = store.facts.get(fid)
        if f is not None:
            terms += keywords(f"{f.predicate.replace('_', ' ')} {f.value}", 3)
    terms += keywords(" ".join(x for x in (card.concrete_event, card.want) if x), 4)
    out: dict[str, None] = {}
    for t in terms:
        out.setdefault(t.lower().strip("'’-"), None)
    return [t for t in out if t]


class RecallIndex:
    def __init__(self, store: ProjectStore):
        self.store = store
        self.path = store.dir / "recall.sqlite"

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        fresh = not self.path.is_file()
        conn = sqlite3.connect(self.path)
        try:
            if not fresh:
                try:
                    conn.execute("SELECT 1 FROM scenes LIMIT 1")
                except sqlite3.DatabaseError:
                    conn.close()
                    self.path.unlink()
                    conn = sqlite3.connect(self.path)
                    fresh = True
            if fresh:
                conn.executescript(DDL)
            with conn:
                yield conn
        finally:
            conn.close()

    def sync(self) -> dict[str, int]:
        """Re-chunk scenes whose effective text changed; drop scenes that no longer exist."""
        stats = {"indexed": 0, "removed": 0, "chunks": 0}
        with self._conn() as conn:
            have = {r[0]: r[1:] for r in conn.execute("SELECT scene_id, version, sha FROM scenes")}
            current = set()
            for sid in self.store.scene_ids():
                got = effective_text(self.store, sid)
                if got is None:
                    continue
                eff, text = got
                current.add(sid)
                if have.get(sid) == (eff.version, eff.sha256):
                    continue
                conn.execute("DELETE FROM chunks WHERE scene_id=?", (sid,))
                for a, b, chunk in chunk_text(text):
                    conn.execute("INSERT INTO chunks VALUES (?,?,?,?,?)", (sid, eff.version, a, b, chunk))
                    stats["chunks"] += 1
                conn.execute("INSERT OR REPLACE INTO scenes VALUES (?,?,?)", (sid, eff.version, eff.sha256))
                stats["indexed"] += 1
            for sid in set(have) - current:
                conn.execute("DELETE FROM chunks WHERE scene_id=?", (sid,))
                conn.execute("DELETE FROM scenes WHERE scene_id=?", (sid,))
                stats["removed"] += 1
        return stats

    def rebuild(self) -> dict[str, int]:
        self.path.unlink(missing_ok=True)
        return self.sync()

    def search(self, terms: list[str], *, scenes: list[str], limit: int = 8) -> list[Passage]:
        if not terms or not scenes:
            return []
        query = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        marks = ",".join("?" * len(scenes))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT scene_id, version, start, end, text, bm25(chunks) FROM chunks "
                f"WHERE chunks MATCH ? AND scene_id IN ({marks}) ORDER BY bm25(chunks) LIMIT ?",
                (query, *scenes, limit),
            ).fetchall()
        return [Passage(r[0], int(r[1]), int(r[2]), int(r[3]), r[4], -float(r[5])) for r in rows]

    def retrieve(self, scene_id: str, *, limit: int = 8, exclude_text: str = "") -> list[Passage]:
        """Ranked passages for drafting `scene_id`, minus anything already present in `exclude_text`."""
        card = self.store.try_read("card", scene_id)
        if card is None:
            return []
        self.sync()
        hits = self.search(
            card_terms(self.store, card), scenes=scenes_before(self.store, scene_id), limit=limit * 2
        )
        norm = re.sub(r"\s+", " ", exclude_text)
        out = []
        for p in hits:
            probe = re.sub(r"\s+", " ", p.text)[:120]
            if probe and probe in norm:
                continue  # already in the recap
            out.append(p)
            if len(out) == limit:
                break
        return out

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            return {
                "scenes": conn.execute("SELECT count(*) FROM scenes").fetchone()[0],
                "chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
            }
