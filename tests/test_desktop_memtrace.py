"""Opt-in memory diagnostics: off without the flag file, with it a baseline and then what grew."""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

import pytest
from PySide6.QtCore import QObject


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_memtrace_is_off_without_the_flag_and_reports_growth_with_it(qapp, tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.desktop import memtrace

    monkeypatch.setenv("APRSBOX_DB_PATH", str(tmp_path / "data" / "aprsbox.db"))
    monkeypatch.setenv("APRSBOX_LOG_DIR", str(tmp_path / "logs"))
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    assert Path(settings.data_dir) == tmp_path / "data"
    parent = QObject()
    assert memtrace.start_if_enabled(parent) is None and not tracemalloc.is_tracing()

    monkeypatch.setattr(memtrace, "BASELINE_DELAY_MS", 50)
    monkeypatch.setattr(memtrace, "INTERVAL_MS", 200)
    (Path(settings.data_dir) / "memtrace.flag").write_text("on", encoding="utf-8")
    try:
        tracer = memtrace.start_if_enabled(parent)
        assert tracer is not None and tracemalloc.is_tracing()
        hoard = []
        log = Path(settings.log_dir) / "memtrace.log"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and log.read_text(encoding="utf-8").count("===") < 3:
            hoard.append(bytearray(200_000))  # something that grows
            qapp.processEvents()
            time.sleep(0.05)
        text = log.read_text(encoding="utf-8")
        assert "baseline" in text and "grown most since the baseline" in text and "object types grown most" in text
        assert "test_desktop_memtrace.py" in text  # the growing line is named
    finally:
        tracemalloc.stop()
        parent.deleteLater()


def test_background_jobs_leave_nothing_behind(qapp) -> None:
    """The leak memtrace found: each run_async() left a decorated slot behind (~90 bytes in Python and more in
    Qt per job, ~4300 jobs an hour). Memory held by tasks.py must not grow with the number of jobs."""
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    from app.desktop import tasks

    owner = QObject()

    def run(count: int) -> int:
        for index in range(count):
            tasks.run_async(lambda: 1, lambda result: None, owner=owner)
            if index % 200 == 0:
                tasks.wait_for_idle()
                qapp.processEvents()
        tasks.wait_for_idle()
        for _ in range(50):
            qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # the relays' deleteLater (no event loop here)
        gc.collect()
        return sum(stat.size for stat in tracemalloc.take_snapshot().statistics("filename")
                   if stat.traceback[0].filename.endswith("tasks.py"))

    tracemalloc.start(1)
    try:
        run(200)
        after_1000 = run(1000)
        after_5000 = run(4000)
    finally:
        tracemalloc.stop()
        owner.deleteLater()
    assert after_5000 - after_1000 < 20_000, f"{after_5000 - after_1000} bytes more after 4000 more jobs"  # old: ~360 KB
    assert tasks.pending_jobs() == 0


def test_garbage_is_collected_on_the_gui_thread_only(qapp) -> None:
    """Qt crash fix: cyclic GC off in every thread, collected by a GUI-thread timer with the usual thresholds."""
    import gc
    import weakref as weak

    from app.desktop.app import GuiThreadGarbageCollector

    was_enabled = gc.isenabled()
    parent = QObject()
    try:
        collector = GuiThreadGarbageCollector(parent)
        assert not gc.isenabled() and collector._timer.isActive() and collector._timer.interval() == 1000

        class Node:
            pass

        first = Node()
        first.me = first  # a cycle only the cyclic collector frees
        probe = weak.ref(first)
        del first
        kept = [Node() for _ in range(gc.get_threshold()[0] + 50)]  # allocations past the gen-0 threshold
        assert probe() is not None  # nobody collects behind our back
        collector.collect()  # what the GUI thread's timer does
        assert probe() is None
        del kept
        collector.stop()
        assert gc.isenabled() and not collector._timer.isActive()  # main() gives automatic GC back on exit
    finally:
        parent.deleteLater()
        if was_enabled:
            gc.enable()


def test_jobs_report_errors_and_skip_gone_owners(qapp) -> None:
    """The single dispatcher: results, errors and a gone owner, with no QObject per job."""
    from PySide6.QtCore import QCoreApplication, QEvent

    from app.desktop import tasks

    hub = tasks.dispatcher()
    results, errors, skipped = [], [], []
    owner = QObject()
    gone = QObject()
    tasks.run_async(lambda: 42, results.append, owner=owner)
    tasks.run_async(lambda: 1 / 0, None, errors.append, owner=owner)
    tasks.run_async(lambda: "late", skipped.append, owner=gone)
    gone.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    tasks.wait_for_idle()
    for _ in range(20):
        qapp.processEvents()
    assert results == [42] and isinstance(errors[0], ZeroDivisionError) and skipped == []
    assert tasks.dispatcher() is hub and tasks.pending_jobs() == 0
    owner.deleteLater()
