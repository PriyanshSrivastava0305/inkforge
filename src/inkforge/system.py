"""OS integration for long runs on a laptop (found the hard way, 2026-10-10).

- `awake_clock()`: seconds of *awake* time. Python's time.monotonic() on Windows is GetTickCount64,
  which keeps counting while the machine sleeps — so a model call in flight when the laptop slept
  "timed out" the moment it woke. Timeouts use this clock instead (QueryUnbiasedInterruptTime on
  Windows, CLOCK_MONOTONIC elsewhere, which already excludes suspend on Linux).
- `keep_awake()`: while a job runs, ask Windows not to idle-sleep (SetThreadExecutionState). It can't
  stop lid-close sleep or a battery-budget hibernate; `power_status()` lets jobs warn about battery.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def awake_clock() -> float:
    if sys.platform == "win32":
        t = ctypes.c_ulonglong()
        if ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(t)):  # type: ignore[attr-defined]
            return t.value / 10_000_000  # 100 ns units
    return time.monotonic()


@contextlib.contextmanager
def keep_awake() -> Iterator[bool]:
    """Prevent idle sleep while the block runs. Yields whether the request was accepted."""
    if sys.platform != "win32":
        yield False
        return
    k = ctypes.windll.kernel32  # type: ignore[attr-defined]
    ok = bool(k.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED))
    try:
        yield ok
    finally:
        k.SetThreadExecutionState(_ES_CONTINUOUS)


@dataclass(frozen=True)
class PowerStatus:
    on_battery: bool | None
    battery_percent: int | None


def power_status() -> PowerStatus:
    if sys.platform != "win32":
        return PowerStatus(None, None)

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    s = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):  # type: ignore[attr-defined]
        return PowerStatus(None, None)
    on_battery = {0: True, 1: False}.get(s.ACLineStatus)
    pct = None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent)
    return PowerStatus(on_battery, pct)
