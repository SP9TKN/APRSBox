"""Application bootstrap: QApplication, database, runtime, login and main window."""

from __future__ import annotations

import argparse
import gc
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QLockFile, QObject, QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.config import settings
from app.db import get_app_setting, init_db, log_event, set_app_setting
from app.desktop import context as ctx
from app.desktop.bootstrap import windows_ui_language
from app.desktop.i18n import set_language, t
from app.desktop.icons import app_icon, clear_caches
from app.desktop.theme import apply_theme
from app.models import UserIdentity

APP_NAME = "APRSBox"
ORGANIZATION = "SQ9MDD"


def apply_current_theme(context: ctx.AppContext) -> None:
    app = QApplication.instance()
    if app is None:
        return
    palette = get_app_setting("ui_palette")
    mode = str(context.ui_value("theme", "dark") or "dark")
    apply_theme(app, palette, mode)
    clear_caches()
    context.signals.theme_changed.emit()
    window = context.main_window
    if window is not None:
        window.sidebar.refresh_chrome()
        for page in list(window.pages.values()):
            handler = getattr(page, "theme_changed", None)
            if callable(handler):
                handler()
            else:
                page.update()


class LoginDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"APRSBox - {t('Login')}")
        application = QApplication.instance()
        self.setWindowIcon(application.windowIcon() if application is not None else app_icon())
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel(t("Sign in"))
        title.setProperty("role", "page-title")
        layout.addWidget(title)
        layout.addWidget(QLabel(t("Username")))
        self.username = QLineEdit()
        layout.addWidget(self.username)
        layout.addWidget(QLabel(t("Password")))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password)
        self.error = QLabel("")
        self.error.setProperty("role", "error")
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        ok = buttons.addButton(t("Login"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(t("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setProperty("variant", "secondary")
        ok.setDefault(True)
        buttons.accepted.connect(self._try_login)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.user: UserIdentity | None = None

    def _try_login(self) -> None:
        from app.auth import authenticate_user, mark_user_login

        user = authenticate_user(self.username.text().strip(), self.password.text())
        if user is None:
            self.error.setText(t("Invalid username or password."))
            self.error.show()
            self.password.clear()
            self.password.setFocus()
            return
        mark_user_login(user.id)
        log_event("INFO", "auth", f"Desktop login for {user.username}")
        self.user = user
        self.accept()


def run_login(parent: QWidget | None, context: ctx.AppContext | None = None) -> UserIdentity | None:
    if not ctx.require_login():
        return _local_user()
    dialog = LoginDialog(parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.user
    return None


def _local_user() -> UserIdentity:
    from app.db import fetch_one

    row = fetch_one("SELECT id, username, role, is_active FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id LIMIT 1")
    if row is None:
        return ctx.LOCAL_USER
    return UserIdentity(id=int(row["id"]), username=str(row["username"]), role="admin", is_active=True)


# The map backgrounds of the desktop (the user's choice), added once to every database - a fresh one
# from the ZIP too - so they are on the map list from the start. Only the settings: no tiles and no
# key come with them. CARTO needs a free key of its own, so its two entries stay off until a key is
# entered (Settings -> Map sources -> API key). A preset whose URL is already there is skipped, and
# one deleted later does not come back.
MAP_PRESETS_SETTING_KEY = "desktop.map_presets_version"
MAP_PRESETS_VERSION = "1"
_CARTO_ATTRIBUTION = ('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors '
                      '&copy; <a href="https://carto.com/attributions">CARTO</a>')
_CARTO_NOTES = ("Wymaga darmowego klucza CARTO (carto.com/basemaps): wpisz go w polu Klucz API "
                "i zaznacz Włączone. Bez klucza kafelki mają napis API key required.")
MAP_PRESETS: tuple[dict[str, str], ...] = (
    {"name": "OpenTopoMap", "url_template": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
     "attribution": ('Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
                     'contributors, SRTM | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'),
     "max_zoom": "17", "subdomains": "a,b,c", "enabled": "1",
     "notes": "Poziomice i cieniowanie rzeźby. Zasady: bez masowego pobierania."},
    {"name": "Wysokości (AWS)", "url_template": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
     "attribution": "Wysokości: AWS Terrain Tiles (Mapzen) · cieniowanie: &copy; Esri", "max_zoom": "15",
     "subdomains": "", "enabled": "1",
     "notes": "Malowana w APRSBox: kolor = wysokość (stała skala) + cieniowanie Esri. Kafelki trzymane na stałe."},
    {"name": "CARTO ciemna", "url_template": "https://{s}.basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png?key={apiKey}",
     "attribution": _CARTO_ATTRIBUTION, "max_zoom": "20", "subdomains": "a,b,c,d", "enabled": "", "notes": _CARTO_NOTES},
    {"name": "CARTO jasna", "url_template": "https://{s}.basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}{r}.png?key={apiKey}",
     "attribution": _CARTO_ATTRIBUTION, "max_zoom": "20", "subdomains": "a,b,c,d", "enabled": "", "notes": _CARTO_NOTES},
    {"name": "Satelita (Esri)", "url_template": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
     "attribution": "Tiles &copy; Esri", "max_zoom": "19", "subdomains": "", "enabled": "1", "notes": ""},
)


def ensure_map_presets() -> None:
    if get_app_setting(MAP_PRESETS_SETTING_KEY) == MAP_PRESETS_VERSION:
        return
    from app.services.map_service import list_map_sources, safe_save_map_source

    existing = {str(source.get("url_template") or "") for source in list_map_sources()}
    for order, preset in enumerate(MAP_PRESETS, start=2):
        if preset["url_template"] in existing:
            continue
        ok, error, _source_id = safe_save_map_source({
            **preset, "min_zoom": "0", "api_key": "", "local_cache_enabled": None, "is_default": None,
            "enabled": preset["enabled"] or None, "sort_order": str(order),
        })
        if not ok:
            log_event("WARNING", "desktop", f"Map preset {preset['name']} was not added: {error}")
    set_app_setting(MAP_PRESETS_SETTING_KEY, MAP_PRESETS_VERSION)


def ensure_first_run_defaults() -> None:
    from app.auth import ensure_admin_user
    from app.db import fetch_one

    row = fetch_one("SELECT COUNT(*) AS total FROM users")
    if row is not None and int(row["total"] or 0) == 0:
        # Same default account as the Linux installer (admin / aprs).
        ensure_admin_user("admin", "aprs")
    if get_app_setting("app_language") is None:
        language = windows_ui_language()
        if language in {"pl", "de", "es", "en"}:
            set_app_setting("app_language", language)
    ensure_map_presets()


class GuiThreadGarbageCollector(QObject):
    """Python's cyclic garbage collector runs only on the GUI thread.

    Left automatic, it runs in whichever thread happens to allocate - often a worker loading page data -
    and frees whatever cycles it finds there, Qt objects of the GUI thread included. A QObject destroyed on
    the wrong thread emits destroyed() into the GUI thread's state: an access violation in Qt6Core
    (the user's station crashed so on 10-17 and 10-23). Automatic collection is switched off and this
    timer collects with the same generation thresholds, once a second, on the GUI thread.
    """

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.collect)
        self._timer.start(1000)

    def stop(self) -> None:
        self._timer.stop()
        gc.enable()

    def collect(self) -> None:
        counts = gc.get_count()
        thresholds = gc.get_threshold()
        if counts[0] < thresholds[0]:
            return
        generation = 0
        if counts[1] >= thresholds[1]:
            generation = 2 if counts[2] >= thresholds[2] else 1
        gc.collect(generation)


_CRASH_LOG = None


def enable_crash_log() -> None:
    """A native crash (the user's station: twice an access violation inside Qt6Core, 10-17 and 10-23) leaves
    no Python trace in pythonw. faulthandler writes every thread's Python stack to logs/crash.log then."""
    global _CRASH_LOG
    import faulthandler
    from datetime import datetime

    try:
        path = Path(settings.log_dir) / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        _CRASH_LOG = open(path, "a", encoding="utf-8")  # kept open for the life of the program
        _CRASH_LOG.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} APRSBox started (pid {os.getpid()})\n")
        _CRASH_LOG.flush()
        faulthandler.enable(file=_CRASH_LOG, all_threads=True)
    except OSError:
        pass


def install_exception_hook() -> None:
    def hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log_event("ERROR", "desktop", f"Unhandled error: {details}")
        except Exception:
            pass
        sys.__stderr__.write(details) if sys.__stderr__ else None
        app = QApplication.instance()
        if app is not None and not getattr(hook, "_showing", False):
            hook._showing = True  # type: ignore[attr-defined]
            try:
                box = QMessageBox()
                box.setIcon(QMessageBox.Icon.Critical)
                box.setWindowTitle("APRSBox")
                box.setText(t("An unexpected error occurred. Details were written to the log."))
                box.setDetailedText(details)
                box.exec()
            finally:
                hook._showing = False  # type: ignore[attr-defined]

    sys.excepthook = hook


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aprsbox", description="APRSBox desktop")
    parser.add_argument("--no-runtime", action="store_true", help="Open the window without starting the APRS runtime.")
    parser.add_argument("--minimized", action="store_true", help="Start minimized to the notification area.")
    parser.add_argument("--page", default="dashboard", help="Page shown on start.")
    return parser.parse_args(argv)


def configure_tooltips() -> None:
    """Tooltips at once (the user: the station tooltip on the map came late and flickered).

    Qt takes Windows' tooltip animation over and rolls every tooltip out - a tall station tooltip
    for hundreds of milliseconds, during which it does not count as visible yet. The map shows the
    tooltip of the station under the mouse on every mouse move, so each small move started a new
    tooltip and the roll over again.
    """
    QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
    QApplication.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationDisplayName("APRSBox")
    configure_tooltips()
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "aprsbox-desktop.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(
            None,
            "APRSBox",
            t("APRSBox is already running for this data folder. Only one instance may run, otherwise frames would be transmitted twice."),
        )
        return 1

    garbage_collector = GuiThreadGarbageCollector(app)  # cyclic GC only on the GUI thread (Qt crash fix)
    install_exception_hook()
    enable_crash_log()
    init_db()
    ensure_first_run_defaults()
    try:
        from app.services.rf_message_log import backfill_rf_message_log

        backfill_rf_message_log()  # once: the RF traffic channel starts with the frames the traffic log keeps
    except Exception as exc:  # noqa: BLE001 - never stops the start
        log_event("WARNING", "messages", f"RF traffic log backfill failed: {exc!r}")
    set_language(get_app_setting("app_language"))
    try:
        # The station's APRS symbol is the program icon from the first window on (like APRSIS32).
        from app.desktop.icons import program_icon
        from app.services.content import get_aprs_symbol_set, get_station_settings

        app.setWindowIcon(program_icon(get_station_settings(include_tx_runtime=False), symbol_set=get_aprs_symbol_set(),
                                       use_station_symbol=ctx.station_symbol_icon_enabled()))
    except Exception:
        pass
    qsettings = QSettings(str(Path(settings.config_dir) / "desktop.ini"), QSettings.Format.IniFormat)
    from app.desktop.runtime import CoreRuntime, set_active_runtime

    runtime = CoreRuntime()
    context = ctx.AppContext(runtime=runtime, qsettings=qsettings)
    apply_current_theme(context)

    user = run_login(None, context)
    if user is None:
        return 0
    context.user = user

    # Always route core_client to the in-process runtime: the desktop never talks HTTP to a core service.
    set_active_runtime(runtime)
    from app.desktop.traffic_logs import TrafficLogWriter

    traffic_log_writer = TrafficLogWriter()
    traffic_log_writer.start()
    from app.desktop.remote_receivers import RemoteReceiverPoller

    remote_receiver = RemoteReceiverPoller()  # idle until a receive page is set in Settings
    remote_receiver.start()
    if not args.no_runtime:
        try:
            runtime.start()
        except Exception as exc:
            QMessageBox.critical(None, "APRSBox", f"{t('APRS runtime failed to start.')}\n\n{exc}")

    from app.desktop.main_window import MainWindow

    window = MainWindow(context)
    from app.desktop import memtrace

    window._memtrace = memtrace.start_if_enabled(window)  # only while <data>/memtrace.flag exists
    geometry = qsettings.value("window_geometry")
    if geometry is not None:
        try:
            window.restoreGeometry(geometry)
        except Exception:
            pass
    window.navigate(args.page or "dashboard")
    if args.minimized or ctx.start_minimized():
        if window.tray is None:
            window.showMinimized()
    else:
        window.show()

    exit_code = app.exec()
    try:
        runtime.stop()
    finally:
        traffic_log_writer.stop()  # after the runtime: no frames arrive any more
        remote_receiver.stop()
        set_active_runtime(None)
        from app.desktop.tasks import wait_for_idle
        from app.desktop.tiles import shutdown_shared_tile_manager

        shutdown_shared_tile_manager()
        wait_for_idle(5000)
        garbage_collector.stop()
        lock.unlock()
    return int(exit_code)
