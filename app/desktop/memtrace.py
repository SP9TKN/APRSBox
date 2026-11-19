"""Opt-in memory diagnostics (the user: APRSBox memory keeps growing ~1.3 MB/h).

Active only while ``<data>/memtrace.flag`` exists. Starts tracemalloc (1 frame); after
BASELINE_DELAY_MS takes a baseline snapshot, then every INTERVAL_MS appends to ``<logs>/memtrace.log``:
the process private bytes, the Python memory tracemalloc sees, the source lines whose memory grew most
since the baseline, and the object types whose count grew most. Python growth that matches the process
growth points at the code; a process growing while Python stays flat points at Qt / native memory.
Remove the flag (and restart) to switch it off.
"""

from __future__ import annotations

import ctypes
import gc
import threading
import time
import tracemalloc
from collections import Counter
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from app.config import settings

BASELINE_DELAY_MS = 15 * 60 * 1000
INTERVAL_MS = 60 * 60 * 1000
TOP_LINES = 25
TOP_TYPES = 15


class _Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def _process_mb() -> tuple[float, float]:
    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # the 64-bit pseudo handle, not a truncated int
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.PrivateUsage / 2**20, counters.WorkingSetSize / 2**20


def _type_counts() -> Counter:
    return Counter(type(item).__qualname__ for item in gc.get_objects())


class MemTrace(QObject):
    def __init__(self, parent: QObject, log_path: Path) -> None:
        super().__init__(parent)
        self.log_path = log_path
        self.base: tracemalloc.Snapshot | None = None
        self.base_types: Counter = Counter()
        self.base_process = (0.0, 0.0)
        self.started = time.monotonic()
        self._busy = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        QTimer.singleShot(BASELINE_DELAY_MS, self._begin)

    def _begin(self) -> None:
        self._timer.start(INTERVAL_MS)  # on the GUI thread: a worker thread cannot start a QTimer
        self._tick()

    def _write(self, lines: list[str]) -> None:
        with open(self.log_path, "a", encoding="utf-8") as log:
            log.write("\n".join(lines) + "\n")

    def _tick(self) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._measure, name="memtrace", daemon=True).start()

    def _measure(self) -> None:
        try:
            snapshot = tracemalloc.take_snapshot().filter_traces((
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
            ))
            types = _type_counts()
            private, working = _process_mb()
            traced, peak = (value / 2**20 for value in tracemalloc.get_traced_memory())
            hours = (time.monotonic() - self.started) / 3600
            stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
            if self.base is None:
                self.base, self.base_types, self.base_process = snapshot, types, (private, working)
                self._write([f"=== {stamp} baseline after {hours:.2f} h: private {private:.0f} MB, working set {working:.0f} MB, "
                             f"python traced {traced:.0f} MB (peak {peak:.0f})"])
                return
            lines = [f"=== {stamp} after {hours:.2f} h: private {private:.0f} MB ({private - self.base_process[0]:+.0f}), "
                     f"working set {working:.0f} MB ({working - self.base_process[1]:+.0f}), "
                     f"python traced {traced:.0f} MB (peak {peak:.0f})",
                     "  grown most since the baseline (source line: size change, block count change):"]
            for stat in snapshot.compare_to(self.base, "lineno")[:TOP_LINES]:
                frame = stat.traceback[0]
                where = f"{Path(frame.filename).name}:{frame.lineno}" if "site-packages" in frame.filename else \
                    f"{frame.filename.replace(str(Path(settings.data_dir).parent), '')}:{frame.lineno}"
                lines.append(f"    {stat.size_diff / 1024:+9.0f} KB  {stat.count_diff:+7d}  {where}")
            grown = Counter({name: types[name] - self.base_types.get(name, 0) for name in types})
            lines.append("  object types grown most: " + ", ".join(f"{name} {diff:+d}" for name, diff in grown.most_common(TOP_TYPES) if diff > 0))
            self._write(lines)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never disturb the station
            try:
                self._write([f"=== {datetime.now():%Y-%m-%d %H:%M} memtrace error: {exc!r}"])
            except OSError:
                pass
        finally:
            self._busy = False


def start_if_enabled(parent: QObject) -> MemTrace | None:
    if not (Path(settings.data_dir) / "memtrace.flag").exists():
        return None
    tracemalloc.start(1)
    log_path = Path(settings.log_dir) / "memtrace.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tracer = MemTrace(parent, log_path)
    tracer._write([f"=== {datetime.now():%Y-%m-%d %H:%M} memtrace on (baseline in {BASELINE_DELAY_MS // 60000} min, "
                   f"then every {INTERVAL_MS // 60000} min)"])
    return tracer
