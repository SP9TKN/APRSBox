"""Is anybody at the computer?

The station runs unattended most of the day while its window stays on screen (the display never
sleeps on the station PC), so pages slow their refreshes down once nobody has touched the mouse
or keyboard for a while (the user's choice).  Any input in the Windows session counts - VS Code,
a remote desktop, other programs - not only input to APRSBox.
"""

from __future__ import annotations

import ctypes
import os
import sys

IDLE_AFTER_SECONDS = 600
# "0" switches the slowdown off: the tests and the screenshot tools set it, so an idle machine
# does not change their timing.
SLOWDOWN_ENV = "APRSBOX_IDLE_SLOWDOWN"


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]


def seconds_since_input() -> float | None:
    """Seconds since the last mouse or keyboard input in this session; None where unknown."""
    if sys.platform != "win32":
        return None
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0  # tick counts wrap after 49.7 days
    except Exception:
        return None


def nobody_at_computer() -> bool:
    if os.environ.get(SLOWDOWN_ENV, "1") == "0":
        return False
    idle = seconds_since_input()
    return idle is not None and idle >= IDLE_AFTER_SECONDS
