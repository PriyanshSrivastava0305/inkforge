"""Rebuildable SQLite index over the project files (docs/04 § SQLite index, ADR-0003).

Files are the source of truth. `rebuild()` drops everything and re-reads the files; `refresh()`
re-derives just the rows affected by one write. Both use the same row builders, so an index kept
up to date incrementally must equal a freshly rebuilt one (tested).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inkforge.storage.workspace import ProjectStore

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE artifacts (kind TEXT, target TEXT, active INT, approved INT, n_versions INT, active_sha TEXT,
                        PRIMARY KEY (kind, target));
CREATE TABLE chapters (id TEXT PRIMARY KEY, ord INT, act_id TEXT, title TEXT, purpose TEXT, has_plan INT);
CREATE TABLE scenes (id TEXT PRIMARY KEY, chapter_id TEXT, narrative_pos INT, card_order INT, pov TEXT,
                     location_id TEXT, temporal_mode TEXT, story_index INT, active_draft INT,
                     approved_draft INT, word_count INT);
CREATE TABLE codex (id TEXT PRIMARY KEY, kind TEXT, name TEXT, aliases TEXT, status TEXT);
CREATE TABLE facts (id TEXT, tbl TEXT, subject_id TEXT, predicate TEXT, value TEXT, valid_from TEXT,
                    valid_to TEXT, canon_status TEXT, PRIMARY KEY (tbl, id));
CREATE TABLE findings (id TEXT PRIMARY KEY, scene_id TEXT, category TEXT, severity TEXT, status TEXT);
CREATE TABLE promises (id TEXT PRIMARY KEY, status TEXT, setup_ref TEXT, payoff_ref TEXT, importance TEXT);
CREATE TABLE jobs (job_id TEXT PRIMARY KEY, stage TEXT, target_id TEXT, status TEXT);
CREATE VIRTUAL TABLE scene_text_fts USING fts5(scene_id UNINDEXED, version UNINDEXED, text);
CREATE VIRTUAL TABLE codex_fts USING fts5(id UNINDEXED, name, aliases, description);
"""
TABLES = [
    "artifacts",
    "chapters",
    "scenes",
    "codex",
    "facts",
    "findings",
    "promises",
    "jobs",
    "scene_text_fts",
    "codex_fts",
]


class ProjectIndex:
    def __init__(self, store: ProjectStore):
        self.store = store
        self.path = store.dir / "index.sqlite"

    # ------------------------------------------------------------ connection
    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self._valid():
            self.rebuild()
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _valid(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            conn = sqlite3.connect(self.path)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            return False
        return row is not None and row[0] == str(SCHEMA_VERSION)

    def rebuild(self) -> None:
        tmp = self.path.with_suffix(".sqlite.tmp")
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(tmp)
        try:
            with conn:
                conn.executescript(DDL)
                conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
                self._all(conn)
        finally:
            conn.close()
        tmp.replace(self.path)

    # ------------------------------------------------------------ incremental
    def refresh(self, kind: str, target: str | None) -> None:
        if not self._valid():
            self.rebuild()
            return
        with self.connect() as conn:
            if kind in ("spec", "design"):
                self._artifact(conn, kind, None)
            elif kind in ("outline", "timeline"):
                self._artifact(conn, kind, None)
                self._chapters(conn)
                self._scenes(conn)
            elif kind == "chapter":
                self._artifact(conn, "chapter", target)
                self._chapters(conn)
            elif kind in ("card", "draft"):
                assert target is not None
                self._artifact(conn, "card", target)
                self._artifact(conn, "draft", target)
                self._scenes(conn)  # narrative positions may shift
                self._scene_text(conn, target)
            elif kind == "codex":
                assert target is not None
                self._codex(conn, target)
            elif kind in ("facts", "review_queue"):
                self._facts(conn)
            elif kind == "findings":
                self._findings(conn)
            elif kind == "promises":
                self._promises(conn)
            elif kind == "job":
                self._jobs(conn)
            else:
                raise ValueError(f"unknown index refresh kind {kind!r}")

    # ------------------------------------------------------------ builders
    def _all(self, conn: sqlite3.Connection) -> None:
        s = self.store
        for kind in ("spec", "design", "outline", "timeline"):
            self._artifact(conn, kind, None)
        for ch in s.chapter_ids():
            self._artifact(conn, "chapter", ch)
        for sid in s.scene_ids():
            self._artifact(conn, "card", sid)
            self._artifact(conn, "draft", sid)
            self._scene_text(conn, sid)
        self._chapters(conn)
        self._scenes(conn)
        for e in s.list_codex():
            self._codex(conn, e.id)
        self._facts(conn)
        self._findings(conn)
        self._promises(conn)
        self._jobs(conn)

    def _artifact(self, conn: sqlite3.Connection, kind: str, target: str | None) -> None:
        t = target or ""
        conn.execute("DELETE FROM artifacts WHERE kind=? AND target=?", (kind, t))
        slot = self.store.slot(kind, target)  # type: ignore[arg-type]
        if not slot.exists():
            return
        m = slot.meta()
        conn.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?)",
            (kind, t, m.active, m.approved, len(m.versions), slot.active_sha()),
        )

    def _chapters(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM chapters")
        s = self.store
        outline = s.try_read("outline")
        entries = {c.id: c for c in outline.chapters} if outline else {}
        for cid in sorted(set(entries) | set(s.chapter_ids())):
            plan = s.try_read("chapter", cid) if cid in s.chapter_ids() else None
            e = entries.get(cid)
            conn.execute(
                "INSERT INTO chapters VALUES (?,?,?,?,?,?)",
                (
                    cid,
                    e.order if e else None,
                    (e.act_id if e else None) or (plan.act_id if plan else None),
                    e.title if e else (plan.title if plan else None),
                    plan.purpose if plan else None,
                    int(plan is not None),
                ),
            )

    def _scenes(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM scenes")
        s = self.store
        timeline = s.try_read("timeline")
        story_idx = timeline.story_index() if timeline else {}
        for pos, sid in enumerate(s.scenes_in_order()):
            card = s.try_read("card", sid)
            dmeta = s.meta("draft", sid)
            words = dmeta.get(dmeta.active).extra.get("word_count") if dmeta.active else None
            conn.execute(
                "INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    sid.split("-sc")[0],
                    pos,
                    card.order if card else None,
                    card.pov_character_id if card else None,
                    card.location_id if card else None,
                    str(card.temporal_mode) if card else None,
                    story_idx.get(sid),
                    dmeta.active,
                    dmeta.approved,
                    words,
                ),
            )

    def _scene_text(self, conn: sqlite3.Connection, sid: str) -> None:
        conn.execute("DELETE FROM scene_text_fts WHERE scene_id=?", (sid,))
        slot = self.store.slot("draft", sid)
        if slot.exists() and (v := slot.meta().active) is not None:
            conn.execute("INSERT INTO scene_text_fts VALUES (?,?,?)", (sid, v, slot.read_text(v)))

    def _codex(self, conn: sqlite3.Connection, eid: str) -> None:
        conn.execute("DELETE FROM codex WHERE id=?", (eid,))
        conn.execute("DELETE FROM codex_fts WHERE id=?", (eid,))
        e = self.store.get_codex(eid)
        if e is None:
            return
        kind = type(e).__name__.lower().replace("loreitem", "lore")
        aliases = json.dumps(sorted(e.aliases), ensure_ascii=False)
        conn.execute(
            "INSERT INTO codex VALUES (?,?,?,?,?)", (e.id, kind, e.name, aliases, str(e.codex_status))
        )
        conn.execute(
            "INSERT INTO codex_fts VALUES (?,?,?,?)", (e.id, e.name, " ".join(e.aliases), e.description or "")
        )

    def _facts(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM facts")
        for tbl in ("facts", "review_queue"):
            for f in getattr(self.store, tbl).all():
                conn.execute(
                    "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)",
                    (
                        f.id,
                        tbl,
                        f.subject_id,
                        f.predicate,
                        json.dumps(f.value),
                        f.valid_from,
                        f.valid_to,
                        str(f.canon_status),
                    ),
                )

    def _findings(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM findings")
        for f in self.store.findings.all():
            conn.execute(
                "INSERT INTO findings VALUES (?,?,?,?,?)",
                (f.id, f.scene_id, f.category, f.severity, f.status),
            )

    def _promises(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM promises")
        for p in self.store.get_promises().promises:
            conn.execute(
                "INSERT INTO promises VALUES (?,?,?,?,?)",
                (p.id, str(p.status), p.setup_ref, p.payoff_ref, p.importance),
            )

    def _jobs(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM jobs")
        for j in self.store.jobs():
            conn.execute("INSERT INTO jobs VALUES (?,?,?,?)", (j.job_id, j.stage, j.target_id, str(j.status)))

    # ------------------------------------------------------------ queries
    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def search_text(self, terms: str, limit: int = 20) -> list[tuple[str, int]]:
        return [
            (r[0], r[1])
            for r in self.query(
                "SELECT scene_id, version FROM scene_text_fts WHERE scene_text_fts MATCH ? ORDER BY rank LIMIT ?",
                (terms, limit),
            )
        ]

    def dump(self) -> dict[str, list[tuple[Any, ...]]]:
        """Every row of every table, deterministically sorted — for rebuild-equivalence checks."""
        with self.connect() as conn:
            return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in TABLES}
