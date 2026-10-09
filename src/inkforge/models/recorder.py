"""Run recorder: runs/<job>/{prompt.txt, raw_output.txt, meta.json, partial.txt} (docs/04, docs/06 § Context manifest).

Everything needed to answer "why did the model do that?" — the exact rendered prompt, every raw
attempt, and the numbers. A cancelled call's text goes to partial.txt and never becomes a version.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from inkforge.domain.common import utcnow
from inkforge.models.types import Generation, Message
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.hashing import sha256_text


def render_messages(messages: list[Message] | tuple[Message, ...]) -> str:
    return "".join(f"### {m.role}\n{m.content}\n\n" for m in messages)


class RunRecorder:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta: dict[str, Any] = {"started_at": utcnow().isoformat(), "attempts": []}
        self._raw: list[str] = []

    def prompt(
        self,
        messages: list[Message] | tuple[Message, ...],
        *,
        prompt_ref: str | None = None,
        profile: str | None = None,
        model: str | None = None,
    ) -> str:
        text = render_messages(messages)
        digest = sha256_text(text)
        atomic_write_text(self.dir / "prompt.txt", text)
        self.meta.update(prompt_ref=prompt_ref, prompt_sha256=digest, profile=profile, model=model)
        self._flush()
        return digest

    def attempt(
        self,
        n: int,
        gen: Generation,
        errors: list[str] | None = None,
        *,
        temperature: float | None = None,
        repair_prompt: str | None = None,
    ) -> None:
        self._raw.append(
            f"===== attempt {n} · finish={gen.finish_reason} · {gen.output_tokens} tok =====\n{gen.text}\n"
        )
        atomic_write_text(self.dir / "raw_output.txt", "\n".join(self._raw))
        rec = {k: v for k, v in asdict(gen).items() if k != "text"}
        rec.update(n=n, errors=errors or [], temperature=temperature, chars=len(gen.text))
        if repair_prompt:
            rec["repair_prompt"] = repair_prompt
        self.meta["attempts"].append(rec)
        if gen.finish_reason == "cancelled":
            atomic_write_text(self.dir / "partial.txt", gen.text)
        self._flush()

    def finish(self, status: str, **extra: Any) -> None:
        self.meta.update(status=status, finished_at=utcnow().isoformat(), **extra)
        self._flush()

    def _flush(self) -> None:
        atomic_write_text(
            self.dir / "meta.json", json.dumps(self.meta, indent=2, ensure_ascii=False, default=str)
        )
