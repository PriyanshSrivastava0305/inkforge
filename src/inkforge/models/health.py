"""Runtime checks for `inkforge doctor` (docs/08 § Operations)."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path

from inkforge.models.router import ModelRouter
from inkforge.models.tokens import TokenizerCache

# Resident sizes measured on this machine (Ollama /api/ps, 2026-10-09), plus headroom for the OS.
APPROX_RESIDENT_GB = {"llama3.1:8b-instruct-q4_K_M": 5.0, "llama3.2:1b": 1.4}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    warn_only: bool = False


def free_ram_gb() -> float | None:
    if sys.platform == "win32":

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        s = MEMORYSTATUSEX()
        s.dwLength = ctypes.sizeof(s)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):  # type: ignore[attr-defined]
            return s.ullAvailPhys / 2**30
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        pass
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("-")[0].split(".") if x.isdigit())


async def runtime_checks(router: ModelRouter, cache_root: Path) -> list[Check]:
    checks: list[Check] = []
    rt = router.config.runtime
    h = await router.health()
    checks.append(Check(f"{h.adapter} reachable", h.reachable, h.error or f"{h.base_url} (v{h.version})"))
    if not h.reachable:
        return checks
    if rt.min_version and h.version and h.version != "fake":
        ok = _version_tuple(h.version) >= _version_tuple(rt.min_version)
        checks.append(
            Check("runtime version", ok, f"{h.version} (tested ≥ {rt.min_version})", warn_only=True)
        )
    needed = sorted({p.model for p in router.config.profiles.values() if p.enabled})
    missing = [m for m in needed if m not in h.available_models]
    checks.append(
        Check(
            "profile models installed", not missing, f"missing: {missing}" if missing else ", ".join(needed)
        )
    )
    checks.append(Check("models resident", True, ", ".join(h.loaded_models) or "none"))
    free = free_ram_gb()
    if free is not None:
        biggest = max(
            (APPROX_RESIDENT_GB.get(m, 0.0) for m in needed if m not in h.loaded_models), default=0.0
        )
        enough = free >= biggest + 0.5
        detail = f"{free:.1f} GB free" + (
            f"; loading the next model needs ~{biggest:.1f} GB" if biggest else ""
        )
        checks.append(Check("free RAM", enough, detail, warn_only=True))
    cache = TokenizerCache(cache_root)
    for fam in sorted(set(rt.tokenizer_family.values())):
        meta = cache.meta(fam)
        tok = cache.get(fam)
        ok = meta is not None and tok is not None
        detail = (
            f"{meta['vocab_size']} tokens, from {meta['source_model']}"
            if ok and meta
            else "missing — run `uv run python scripts/build_tokenizer.py` (falls back to estimates)"
        )
        checks.append(Check(f"tokenizer cache ({fam})", ok, detail, warn_only=True))
    return checks
