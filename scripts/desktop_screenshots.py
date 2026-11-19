"""Render desktop pages off-screen to PNG files (visual checks without a display).

Usage:
    python scripts/desktop_screenshots.py OUT_DIR [page[:arg] ...] [--tiles] [--light] [--palette=NAME] [--paths] [--full] [--band] [--alerts]

Uses an isolated temporary database seeded with simulated traffic; nothing is
transmitted and no APRS runtime is started.  Map tiles are disabled unless
``--tiles`` is given.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    flags = {arg for arg in sys.argv[1:] if arg.startswith("--")}
    palette = None
    for arg in sys.argv[1:]:
        if arg.startswith("--palette="):
            palette = arg.split("=", 1)[1]
    if not args:
        print(__doc__)
        return 2
    out_dir = Path(args[0])
    pages = args[1:] or ["dashboard", "map", "stations", "traffic"]
    out_dir.mkdir(parents=True, exist_ok=True)

    import desktop_support

    desktop_support.isolated_environment()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.db import execute, set_app_setting

    seeded = desktop_support.seed_demo_database(with_alerts="--alerts" in flags)
    if "--tiles" not in flags:
        # Offline: point every tile source at a closed local port.
        execute("UPDATE map_sources SET url_template = 'http://127.0.0.1:9/{z}/{x}/{y}.png', local_cache_enabled = 0")
    if palette:
        set_app_setting("ui_palette", palette)
    if "--band" in flags:
        # Band condition only lists enabled RF interfaces; the runtime is never started here.
        execute("UPDATE modems SET enabled = 1 WHERE id = ?", (seeded["interface_id"],))
    from app.desktop import context as ctx
    from app.desktop.app import apply_current_theme
    from app.desktop.i18n import set_language
    from app.desktop.main_window import MainWindow
    from app.desktop.tasks import wait_for_idle

    set_language("pl")
    settings = QSettings(str(Path(os.environ["APRSBOX_CONFIG_DIR"]) / "desktop.ini"), QSettings.Format.IniFormat)
    context = ctx.AppContext(runtime=None, qsettings=settings)
    if "--light" in flags:
        context.set_ui_value("theme", "light")
    if "--paths" in flags:
        context.set_ui_value("map/rf_paths_visible", True)
        context.set_ui_value("map/view", '{"latitude": 50.05, "longitude": 19.75, "zoom": 9}')
    apply_current_theme(context)
    window = MainWindow(context)
    window.resize(1440, 900)
    window.show()

    def settle(seconds: float = 1.5) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            wait_for_idle(50)
            app.processEvents()
            time.sleep(0.02)

    settle(1.0)
    for page in pages:
        argument = None
        if ":" in page:
            page, argument = page.split(":", 1)
        window.navigate(page, argument)
        settle(2.5 if page == "map" else 1.5)
        target = out_dir / f"{page}{'-' + argument if argument else ''}.png"
        window.grab().save(str(target))
        print(f"saved {target}")
        current = window.current_page()
        if "--full" in flags and current is not None and getattr(current, "scroll", None) is not None:
            # Whole scrollable body at the viewport width (content below the fold).
            body = current.body_widget
            width = current.scroll.viewport().width()
            body.resize(width, max(body.heightForWidth(width), body.sizeHint().height()))
            settle(0.5)
            full_target = out_dir / f"{page}{'-' + argument if argument else ''}-full.png"
            body.grab().save(str(full_target))
            print(f"saved {full_target}")
    print(f"seeded frames accepted: {seeded['accepted']}")
    from app.db import fetch_all

    # Background loaders log their failures instead of raising; surface them here.
    for row in fetch_all("SELECT level, category, message FROM event_logs WHERE level IN ('ERROR', 'WARNING') ORDER BY id"):
        print(f"event log {row['level']} [{row['category']}]: {row['message']}")
    window.alert_watcher.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
