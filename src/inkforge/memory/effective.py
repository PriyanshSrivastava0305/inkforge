"""Effective scene versions (D34): what memory is built from.

A scene's effective version is its approved version if it has one, otherwise its active draft
(provisional). Everything in `inkforge.memory` is derived from these and keyed by their content
hash, so approving, editing or redrafting a scene makes the derived data stale automatically.
"""

from __future__ import annotations

from dataclasses import dataclass

from inkforge.storage.workspace import ProjectStore


@dataclass(frozen=True)
class EffectiveScene:
    scene_id: str
    version: int
    sha256: str
    provisional: bool  # True = not approved yet (active draft)

    @property
    def ref(self) -> str:
        return f"draft:{self.scene_id}@v{self.version}"


def effective(store: ProjectStore, scene_id: str) -> EffectiveScene | None:
    m = store.meta("draft", scene_id)
    v = m.approved if m.approved is not None else m.active
    if v is None:
        return None
    return EffectiveScene(scene_id, v, m.get(v).sha256, provisional=m.approved is None)


def effective_text(store: ProjectStore, scene_id: str) -> tuple[EffectiveScene, str] | None:
    eff = effective(store, scene_id)
    if eff is None:
        return None
    return eff, store.read("draft", scene_id, version=eff.version)


def scenes_before(store: ProjectStore, scene_id: str) -> list[str]:
    """Scenes the reader has already seen when `scene_id` begins (narrative order)."""
    order = store.scenes_in_order()
    return order[: order.index(scene_id)] if scene_id in order else order
