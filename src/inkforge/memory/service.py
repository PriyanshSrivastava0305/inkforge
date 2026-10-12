"""Memory service: one place to ask "is memory up to date?" and to bring it up to date.

All three tiers are derived from the manuscript and structured records (docs/06):
  DIGEST  memory/digests/**.json     model-written (or extracted) summaries, keyed by source hash
  RECALL  recall.sqlite              paragraph chunks of effective scene versions (FTS5)
  CORE    built on demand per scene; `memory/core.json` is a snapshot at the end of the book
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from inkforge.memory.core import build_core
from inkforge.memory.digests import Digest, Digester, DigestStore
from inkforge.memory.effective import effective
from inkforge.memory.recall import RecallIndex
from inkforge.models.router import ModelRouter
from inkforge.models.tokens import estimate
from inkforge.models.types import CancelToken
from inkforge.prompts.registry import PromptRegistry
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ProjectStore


@dataclass
class MemoryStatus:
    scenes_with_text: int
    provisional: int
    digests_fresh: int
    digests_stale: list[str] = field(default_factory=list)
    extract_digests: int = 0
    recall_scenes: int = 0
    recall_chunks: int = 0

    @property
    def up_to_date(self) -> bool:
        return not self.digests_stale and self.recall_scenes == self.scenes_with_text


class MemoryService:
    def __init__(
        self, store: ProjectStore, router: ModelRouter | None = None, prompts: PromptRegistry | None = None
    ):
        self.store, self.router = store, router
        self.digests = DigestStore(store)
        self.digester = Digester(store, router, prompts)
        self.recall = RecallIndex(store)

    def status(self) -> MemoryStatus:
        effs = [e for e in (effective(self.store, s) for s in self.store.scenes_in_order()) if e]
        stale = self.digests.stale()
        fresh = [
            d
            for k, t in self.digests.all_targets()
            if self.digests.is_fresh(k, t) and (d := self.digests.get(k, t))
        ]
        r = self.recall.stats() if self.recall.path.is_file() else {"scenes": 0, "chunks": 0}
        return MemoryStatus(
            scenes_with_text=len(effs),
            provisional=sum(e.provisional for e in effs),
            digests_fresh=len(fresh),
            digests_stale=[f"{k}:{t}" for k, t in stale],
            extract_digests=sum(d.method == "extract" for d in fresh),
            recall_scenes=r["scenes"],
            recall_chunks=r["chunks"],
        )

    async def update(
        self, *, use_model: bool = True, cancel: CancelToken | None = None, on_built=None
    ) -> list[Digest]:
        """Bring digests and the recall index up to date; write the end-of-book CORE snapshot."""
        built = await self.digester.update(use_model=use_model, cancel=cancel, on_built=on_built)
        self.recall.sync()
        self.snapshot_core()
        return built

    async def rebuild(
        self, *, use_model: bool = True, cancel: CancelToken | None = None, on_built=None
    ) -> list[Digest]:
        """Throw away every derived memory artifact and regenerate it from the files."""
        self.digests.clear()
        self.recall.path.unlink(missing_ok=True)
        return await self.update(use_model=use_model, cancel=cancel, on_built=on_built)

    async def upgrade_extracts(self, *, cancel: CancelToken | None = None, on_built=None) -> list[Digest]:
        """Consolidation: replace stand-in extract digests with model-written ones (children first)."""
        built = []
        for kind, target in self.digests.all_targets():
            d = self.digests.get(kind, target)
            if d is not None and d.method == "extract" and self.digests.source(kind, target) is not None:
                new = await self.digester.build(kind, target, use_model=True, cancel=cancel)
                if new is not None and new.method == "model":
                    built.append(new)
                    if on_built:
                        on_built(new)
        return built

    def snapshot_core(self) -> None:
        order = self.store.scenes_in_order()
        if not order:
            return
        count = self.router.tokens.count if self.router else None
        model = self.router.profile("prose").model if self.router else ""
        block = build_core(self.store, order[-1], (lambda t: count(t, model).n) if count else estimate)
        data = {
            "as_of_scene": order[-1],
            "lines": [{"text": ln.text, "sources": ln.sources} for ln in block.lines],
            "dropped": block.dropped,
        }
        with self.store.lock:
            atomic_write_text(
                self.store.path("memory/core.json"), json.dumps(data, indent=2, ensure_ascii=False)
            )
