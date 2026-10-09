"""Append-only JSONL audit log per project (docs/04 § AuditEvent)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from inkforge.domain.common import Actor
from inkforge.domain.models import AuditEvent


class AuditLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, event: AuditEvent) -> AuditEvent:
        line = event.model_dump_json() + "\n"
        # O_APPEND: each write lands at the end even if another handle appended meanwhile.
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return event

    def record(self, operation: str, target: str, *, actor: Actor = Actor.SYSTEM, **kw) -> AuditEvent:
        return self.append(AuditEvent(actor=actor, operation=operation, target=target, **kw))

    def __iter__(self) -> Iterator[AuditEvent]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield AuditEvent.model_validate_json(line)
