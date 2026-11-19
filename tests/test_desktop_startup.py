"""The real desktop entry point (app.desktop.app.main) starts and quits cleanly."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import desktop_support  # noqa: E402

ENV_KEYS = ("APRSBOX_DB_PATH", "APRSBOX_LOG_DIR", "APRSBOX_CONFIG_DIR", "APRSBOX_BACKUPS_DIR")


def test_main_opens_window_and_exits(tmp_path: Path) -> None:
    saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
    saved_hook = sys.excepthook
    desktop_support.isolated_environment(tmp_path)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.desktop import i18n
    from app.desktop.app import main
    from app.desktop.main_window import MainWindow

    seen: dict[str, object] = {"ticks": 0}

    def inspect_and_quit() -> None:
        # quit() before app.exec() starts is ignored, so keep asking until the loop ends.
        seen["ticks"] = int(seen["ticks"]) + 1
        windows = [widget for widget in app.topLevelWidgets() if isinstance(widget, MainWindow)]
        if windows:
            seen["windows"] = len(windows)
            seen["page"] = windows[-1].current_key
        if windows and int(seen["ticks"]) >= 4 or int(seen["ticks"]) > 100:
            app.quit()

    ticker = QTimer()
    ticker.setInterval(300)
    ticker.timeout.connect(inspect_and_quit)
    ticker.start()
    try:
        exit_code = main(["--no-runtime", "--page", "map"])
    finally:
        ticker.stop()
        sys.excepthook = saved_hook
        for widget in app.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget._really_quit = True
                widget._chrome_timer.stop()
                widget.alert_watcher.stop()
                for page in widget.pages.values():
                    page.deactivate()
                if widget.tray is not None:
                    widget.tray.hide()
                widget.hide()
                widget.deleteLater()
        app.processEvents()
        i18n._language = None  # noqa: SLF001
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert exit_code == 0
    assert seen.get("windows", 0) >= 1
    assert seen.get("page") == "map"
    # First run created the database and the desktop lock was released.
    assert (tmp_path / "data" / "aprsbox.db").exists()
    from PySide6.QtCore import QLockFile

    lock = QLockFile(str(tmp_path / "data" / "aprsbox-desktop.lock"))
    assert lock.tryLock(100)
    lock.unlock()
