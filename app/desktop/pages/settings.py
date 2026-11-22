"""Settings page (web: templates/settings.html).

Web-only parts are adapted: HTTPS certificates and the git based self-update do
not apply to the desktop program; "Restart services" restarts the in-process
APRS runtime and the host power actions call Windows' shutdown command.
A "Desktop application" section holds the window-only options.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.datetime_utils import format_display_datetime
from app.desktop import context as ctx
from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon_pixmap
from app.desktop.tasks import SerialLoader, run_async
from app.desktop.widgets.common import (
    Card,
    FlashMessage,
    FormGrid,
    Page,
    button,
    checkbox,
    combo,
    combo_value,
    confirm,
    icon_button,
    key_value_grid,
    label,
    line_edit,
    set_combo_value,
    spin,
    text_edit,
)
from app.desktop.widgets.crud import RowActionBar
from app.desktop.widgets.table import Column, DataTable

def format_megabytes(megabytes: float) -> str:
    if megabytes >= 1024:
        return f"{megabytes / 1024:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " GB"
    return f"{megabytes:.0f} MB"


DATABASE_TABLE_LABELS: dict[str, str] = {
    "event_logs": "Event logs",
    "traffic_frames": "Traffic frames",
    "digi_flow_event_log": "DIGI flow event log",
    "aprsis_igate_rf_heard": "IGate RF heard state",
    "aprsis_igate_station_state": "IGate Internet station state",
    "aprsis_igate_pending_position": "IGate pending sender positions",
    "traffic_device_station_device_hourly": "Traffic devices hourly stats",
    "radio_activity_5m": "Radio activity buckets",
    "aprsis_uplink_minute_stats": "APRS-IS uplink minute stats",
    "aprsis_uplink_stats": "APRS-IS uplink counters",
    "wx_runtime_cache": "WX runtime cache",
    "band_condition_audibility_buckets": "Band condition audibility buckets",
    "band_condition_activity_station_buckets": "Band condition station buckets",
    "band_condition_activity_buckets": "Band condition activity buckets",
    "band_condition_station_hours": "Band condition hourly station observations",
    "band_condition_station_profiles": "Band condition learned station profiles",
    "band_condition_hourly": "Band condition hourly history",
    "map_station_state": "Map station state",
    "weather_observations": "Weather history (charts)",
    "notification_radar_state": "Notification radar state",
}
EVENT_LOG_MIN_LEVEL_OPTIONS = ("INFO", "WARNING", "ERROR")
CONFIG_BACKUP_MAX_BYTES = 5 * 1024 * 1024


def format_size_bytes(size_bytes: Any) -> str:
    value = float(max(0, int(size_bytes or 0)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return "0 B"


def format_ratio_percent(ratio: Any) -> str:
    return f"{max(0.0, float(ratio or 0.0)) * 100:.1f}%"


def format_retention(minutes: int) -> str:
    hours, rest = divmod(int(minutes), 60)
    return f"{hours}h" if rest == 0 else f"{hours}h {rest}m"


class PaletteSwatch(QWidget):
    """Small preview of a colour palette (web: .palette-picker-swatch)."""

    def __init__(self, palette_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_name = palette_name
        self.setFixedSize(QSize(54, 26))

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        from app.desktop.theme import current_colors, theme_colors

        mode = "dark" if current_colors().is_dark else "light"
        colors = theme_colors(self.palette_name, mode)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(colors.color("border"))
        painter.setBrush(colors.color("bg"))
        painter.drawRoundedRect(rect, 6, 6)
        third = rect.width() / 3
        for index, key in enumerate(("panel", "accent", "warning")):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.color(key)))
            painter.drawEllipse(int(rect.left() + third * index + third / 2 - 6), int(rect.center().y() - 6), 12, 12)
        painter.end()


class SettingsPage(Page):
    page_key = "settings"

    def build(self) -> None:
        self.manage = self.can_edit
        self.view: dict[str, Any] = {}
        self.map_source_id: int | None = None
        self._loaded_forms = False

        self._build_global()
        self._build_desktop()
        self._build_cwop()
        self._build_version()
        self._build_device_identification()
        self._build_backup()
        self._build_alarms()
        self._build_map_sources()
        self._build_database()
        self._build_danger_zone()
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)

    # ============================================================ sections
    def _section(self, title: str, help_page: str | None = None, subtitle: str | None = None) -> tuple[Card, FlashMessage]:
        card = Card(title, subtitle)
        if help_page:
            card.add_action(icon_button("help-circle-outline", t("Help"), lambda page=help_page: show_help(self, page)))
        flash = FlashMessage()
        card.add(flash)
        self.body.addWidget(card)
        return card, flash

    def _build_global(self) -> None:
        from app.db import TRAFFIC_RETENTION_ALLOWED_MINUTES
        from app.i18n import get_supported_languages
        from app.ui_palette import get_ui_palette_options

        card, self.global_flash = self._section(t("Global Settings"), "application/settings_global")
        grid = FormGrid(3)
        self.language = combo([(item["code"], item["label"]) for item in get_supported_languages()])
        grid.add_field("language", t("Language"), self.language)
        self.units = combo([("metric", "Metric"), ("imperial", "Imperial")])
        grid.add_field("default_units", t("Units"), self.units)
        self.retention = combo([(value, format_retention(value)) for value in TRAFFIC_RETENTION_ALLOWED_MINUTES])
        grid.add_field("traffic_retention_minutes", t("Traffic history retention"), self.retention)
        self.symbol_set = combo([("legacy", "Legacy icon set"), ("modern", "Modern icon set")])
        grid.add_field("aprs_symbol_set", t("Icon set"), self.symbol_set)
        self.log_level = combo([(value, value) for value in EVENT_LOG_MIN_LEVEL_OPTIONS])
        grid.add_field("event_log_min_level", t("Minimum stored log level"), self.log_level)
        self.debug_logs = checkbox(t("Enable DEBUG logs"))
        grid.add_widget(self.debug_logs, span=1)
        card.add(grid)
        card.add(label(t("Color palette"), "h3"))
        palette_grid = QGridLayout()
        palette_grid.setHorizontalSpacing(14)
        palette_grid.setVerticalSpacing(6)
        self.palette_group = QButtonGroup(self)
        self.palette_buttons: dict[str, QRadioButton] = {}
        for index, option in enumerate(get_ui_palette_options()):
            value = str(option.get("value"))
            cell = QHBoxLayout()
            radio = QRadioButton(t(option.get("label") or value))
            self.palette_group.addButton(radio)
            self.palette_buttons[value] = radio
            cell.addWidget(PaletteSwatch(value))
            cell.addWidget(radio)
            cell.addStretch(1)
            palette_grid.addLayout(cell, index // 4, index % 4)
        card.add(palette_grid)
        map_grid = FormGrid(3)
        self.coverage_fill = combo([(value, f"{value}%") for value in range(0, 21)])
        map_grid.add_field("coverage_fill_opacity", t("Coverage fill opacity"), self.coverage_fill)
        self.coverage_outline = combo([(value, f"{value}%") for value in range(0, 101, 10)])
        map_grid.add_field("coverage_outline_opacity", t("Coverage outline opacity"), self.coverage_outline)
        self.spiderfy_levels = spin(2, 0, 10)
        map_grid.add_field("map_marker_spiderfy_zoom_levels", t("Activate X levels before maximum zoom"), self.spiderfy_levels)
        self.spiderfy_distance = spin(20, 1, 100, suffix=" px")
        map_grid.add_field("map_marker_spiderfy_nearby_distance_px", t("Overlapping marker distance [px]"), self.spiderfy_distance)
        self.clustering = checkbox(t("Group overlapping station icons on the map"))
        map_grid.add_widget(self.clustering)
        self.spiderfy = checkbox(t("Enable spreading overlapping markers"))
        map_grid.add_widget(self.spiderfy)
        card.add(map_grid)
        actions = QHBoxLayout()
        actions.addWidget(button(t("Save Global Settings"), icon_name="content-save-outline", on_click=self._save_global))
        actions.addStretch(1)
        card.add(actions)
        for widget in (grid, map_grid):
            widget.setEnabled(self.manage)
        for radio in self.palette_buttons.values():
            radio.setEnabled(self.manage)
        if not self.manage:
            card.add(label(t("Your role can view global settings but cannot change them."), "muted"))

    def _build_desktop(self) -> None:
        from app.desktop import autostart

        card, self.desktop_flash = self._section(t("Desktop application"), None,
                                                 t("Options of this window: sign-in, notification area and start with Windows."))
        self.require_login = checkbox(t("Require sign-in when the window starts"))
        self.minimize_tray = checkbox(t("Closing the window minimizes it to the notification area"))
        self.start_minimized = checkbox(t("Start minimized to the notification area"))
        self.alert_sound = checkbox(t("Play a sound for emergency and alarm pop-ups"))
        self.station_icon = checkbox(t("Use my station's APRS symbol as the program icon (like APRSIS32)"))
        self.autostart = checkbox(t("Start APRSBox with Windows"))
        self.autostart.setVisible(autostart.is_supported())
        for widget in (self.require_login, self.minimize_tray, self.start_minimized, self.alert_sound, self.station_icon, self.autostart):
            card.add(widget)
            widget.setEnabled(self.manage)
        card.add(label(t("Default account when no users exist: admin / aprs. Change its password in Users / Roles."), "small", wrap=True))
        # Map tiles kept on disk (the user's choice: a limit to pick, 2 GB by default).
        from app.desktop.tiles import TILE_CACHE_LIMIT_OPTIONS_MB

        limit_grid = FormGrid(3)
        self.tile_cache_limit = combo([(value, format_megabytes(value)) for value in TILE_CACHE_LIMIT_OPTIONS_MB])
        self.tile_cache_limit.setEnabled(self.manage)
        limit_grid.add_field("tile_cache_limit", t("Map tiles on disk: limit"), self.tile_cache_limit)
        card.add(limit_grid)
        self.tile_cache_used = label("", "small", wrap=True)
        card.add(self.tile_cache_used)
        card.add(label(t("Downloaded map tiles stay on disk, so the map also works offline. Over the limit, tiles "
                         "outside the listening area of my station go first, then the oldest ones. The elevation "
                         "map is painted once and kept."), "small", wrap=True))
        self.fast_map = checkbox(t("Fast map: keep map tiles in memory (about 130 MB more RAM)"))
        self.fast_map.setEnabled(self.manage)
        card.add(self.fast_map)
        # Another station's receive page (SP9DLM's APRS ANALIZER) as an extra listener for the traffic analyzer.
        remote_grid = FormGrid(1)
        self.remote_receiver = line_edit("", placeholder="http://station.example:8080")
        self.remote_receiver.setEnabled(self.manage)
        remote_grid.add_field("remote_receiver", t("Remote receiver page (APRS ANALIZER)"), self.remote_receiver)
        card.add(remote_grid)
        card.add(label(t("APRSBox reads the page's list of heard stations every minute and keeps it; the traffic "
                         "analyzer then shows whether that station heard a callsign, directly or through which "
                         "digipeater. Read only. Empty = off."), "small", wrap=True))
        card.add(label(t("Off (a light app): only the tiles on the screen stay in memory, and a map that is not shown "
                         "gives its memory back. On: up to 512 tiles stay in memory, so moving and zooming back to "
                         "places seen before is instant."), "small", wrap=True))
        actions = QHBoxLayout()
        actions.addWidget(button(t("Save"), icon_name="content-save-outline", on_click=self._save_desktop))
        actions.addStretch(1)
        card.add(actions)

    def _build_cwop(self) -> None:
        card, self.cwop_flash = self._section(t("CWOP weather stations"), None,
                                              t("Weather stations of the CWOP network on the map and in the traffic monitor."))
        self.cwop_enabled = checkbox(t("Show CWOP weather stations (cwop.aprs.net, receive only)"))
        card.add(self.cwop_enabled)
        self.cwop_range = label("", "small", wrap=True)
        card.add(self.cwop_range)
        self.cwop_status = label("", "small", wrap=True)
        card.add(self.cwop_status)
        card.add(label(t("CWOP stations (CW..., DW..., EW..., FW...) do not report to APRS-IS, so APRSBox opens a "
                         "separate read-only connection for them, like APRSIS32. Nothing is transmitted, gated "
                         "or digipeated."), "small", wrap=True))
        actions = QHBoxLayout()
        actions.addWidget(button(t("Save"), icon_name="content-save-outline", on_click=self._save_cwop))
        actions.addStretch(1)
        card.add(actions)
        self.cwop_enabled.setEnabled(self.manage)

    def _render_cwop_status(self) -> None:
        status = self.view.get("cwop_status") or {}
        state = status.get("status") or ""
        detail = status.get("detail") or ""
        if state == "connected":
            text = t("Connected to cwop.aprs.net (receive only).")
        elif state == "connecting":
            text = t("Connecting to cwop.aprs.net...")
        elif state == "error":
            text = f"{t('Connection error')}: {detail}"
        else:
            text = t("Not connected.")
        self.cwop_status.setText(f"{t('State')}: {text}")
        range_km = int((self.view.get("cwop") or {}).get("range_km") or 0)
        self.cwop_range.setText(tf("Range: {km} km around my station, the same radius as the APRS-IS filter.", km=range_km))

    def _save_cwop(self) -> None:
        from PySide6.QtCore import QTimer

        from app.services.cwop import save_cwop_settings

        save_cwop_settings(enabled=self.cwop_enabled.isChecked())
        self.cwop_flash.success(t("Saved. The connection to the CWOP server follows within a few seconds."))
        QTimer.singleShot(3000, self.loader.request)  # the new connection state

    def _build_version(self) -> None:
        card, self.version_flash = self._section(t("Application version"), None)
        self.version_info = QVBoxLayout()
        card.add(self.version_info)
        actions = QHBoxLayout()
        self.check_version_button = button(t("Check upstream version"), variant="secondary", icon_name="cloud-search-outline",
                                           on_click=self._check_version)
        actions.addWidget(self.check_version_button)
        actions.addStretch(1)
        card.add(actions)
        card.add(label(t("The Windows version is updated by replacing the program folder; settings and data are kept in the data directory."),
                       "small", wrap=True))

    def _build_device_identification(self) -> None:
        card, self.device_flash = self._section(t("APRS device identification"), "application/settings_device_identification")
        self.device_info = QVBoxLayout()
        card.add(self.device_info)
        if self.manage:
            actions = QHBoxLayout()
            actions.addWidget(button(t("Update now"), icon_name="download", on_click=self._update_device_identification))
            actions.addStretch(1)
            card.add(actions)
        else:
            card.add(label(t("Your role can view APRS device identification status but cannot start updates."), "muted"))

    def _build_backup(self) -> None:
        card, self.backup_flash = self._section(t("Configuration backup"), "application/settings_configuration_backup")
        if self.manage:
            actions = QHBoxLayout()
            actions.addWidget(button(t("Export configuration backup"), variant="secondary", icon_name="export",
                                     on_click=self._export_backup))
            actions.addWidget(button(t("Import configuration backup"), icon_name="import", on_click=self._import_backup))
            actions.addStretch(1)
            card.add(actions)
            card.add(label(t("Import rewrites current configuration tables. Keep an exported file before restoring another snapshot. After import, restart services from Danger zone."),
                           "muted", wrap=True))
        else:
            card.add(label(t("Your role can view backup information but cannot run export/import actions."), "muted"))

    def _build_alarms(self) -> None:
        from app.services.alarm_groups import APRS_ALARM_LEVEL_THRESHOLDS
        from app.services.alert_event_icons import ALERT_EVENT_CATEGORIES

        card, self.alarm_flash = self._section(t("APRS alarm settings"), "application/settings_alarms")
        self.alarm_enabled = checkbox(t("Enable APRS alarms"))
        card.add(self.alarm_enabled)
        card.add(label(t("Alarm groups")))
        self.alarm_groups = line_edit("")
        self.alarm_groups.textEdited.connect(lambda text: self.alarm_groups.setText(text.upper()) if text != text.upper() else None)
        card.add(self.alarm_groups)
        card.add(label(t("Alarm thresholds by event type"), "h3"))
        thresholds = QGridLayout()
        thresholds.setHorizontalSpacing(16)
        thresholds.setVerticalSpacing(6)
        thresholds.addWidget(label(t("Type"), "muted"), 0, 0)
        thresholds.addWidget(label(t("Alerts"), "muted"), 0, 1)
        thresholds.addWidget(label(t("Alert popup"), "muted"), 0, 2)
        level_options = [("off", t("Off"))] + [(str(level), f"≥ {level}") for level in APRS_ALARM_LEVEL_THRESHOLDS]
        self.threshold_rows: dict[str, tuple[QComboBox, QComboBox]] = {}
        from app.desktop.theme import current_colors

        for row, category in enumerate(ALERT_EVENT_CATEGORIES, start=1):
            caption = QHBoxLayout()
            icon_label = label("")
            icon_label.setPixmap(icon_pixmap(str(category.get("icon") or "alert-outline.svg").removesuffix(".svg"),
                                             current_colors().color("text"), 18))
            caption.addWidget(icon_label)
            caption.addWidget(label(t(category.get("label") or category.get("key"))))
            caption.addStretch(1)
            thresholds.addLayout(caption, row, 0)
            alerts = combo(level_options)
            popup = combo(level_options)
            for widget in (alerts, popup):
                widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            thresholds.addWidget(alerts, row, 1)
            thresholds.addWidget(popup, row, 2)
            self.threshold_rows[str(category["key"])] = (alerts, popup)
        thresholds.setColumnStretch(3, 1)
        card.add(thresholds)
        actions = QHBoxLayout()
        actions.addWidget(button(t("Save alarm settings"), icon_name="content-save-outline", on_click=self._save_alarms))
        actions.addStretch(1)
        card.add(actions)
        self.alarm_info = QVBoxLayout()
        card.add(self.alarm_info)
        for widget in (self.alarm_enabled, self.alarm_groups):
            widget.setEnabled(self.manage)
        for alerts, popup in self.threshold_rows.values():
            alerts.setEnabled(self.manage)
            popup.setEnabled(self.manage)

    def _build_map_sources(self) -> None:
        card, self.map_flash = self._section(t("Map sources"), "application/settings_map_sources")
        self.map_table = DataTable(
            [
                Column("order", t("Lp."), width=50),
                Column("name", t("Name"), stretch=True, bold=lambda row: bool(row.get("is_default")),
                       secondary=lambda row: str(row.get("url_template") or "")),
                Column("zoom", t("Zoom"), width=80, display=lambda row: f"{row.get('min_zoom')} - {row.get('max_zoom')}"),
                Column("enabled", t("Enabled"), width=90, display=lambda row: t("Yes") if row.get("enabled") else t("No")),
                Column("is_default", t("Default"), width=90, display=lambda row: t("Default") if row.get("is_default") else "-"),
                Column("cache", t("Cache"), width=170,
                       display=lambda row: (f"{tf('{count} tiles', count=row.get('cache_tile_count') or 0)} ({row.get('cache_size_label') or '0 B'})"
                                            if row.get("local_cache_enabled") else "-")),
            ],
            row_height=42,
            sortable=False,
        )
        self.map_table.set_empty_text(t("No records yet."))
        self.map_table.setMinimumHeight(200)
        card.add(self.map_table)
        self.map_actions = RowActionBar(self.map_table)
        if self.manage:
            self.map_table.row_activated.connect(self._edit_map_source)
            self.map_actions.add(t("Move up"), "arrow-up", lambda row: self._move_map_source(row, "up"),
                                 enabled_for=lambda row: int(row.get("order") or 1) > 1)
            self.map_actions.add(t("Move down"), "arrow-down", lambda row: self._move_map_source(row, "down"),
                                 enabled_for=lambda row: int(row.get("order") or 0) < len(self.view.get("map_sources") or []))
            self.map_actions.add(t("Set as default"), "star-outline", self._default_map_source,
                                 enabled_for=lambda row: not row.get("is_default"))
            self.map_actions.add(t("Edit"), "pencil", self._edit_map_source)
            self.map_actions.add(t("Clear cache"), "broom", self._clear_map_cache, enabled_for=lambda row: bool(row.get("local_cache_enabled")))
            self.map_actions.add(t("Delete"), "delete-outline", self._delete_map_source, variant="danger")
            card.add(self.map_actions)
            form = FormGrid(2)
            self.ms_name = line_edit("")
            form.add_field("name", t("Name"), self.ms_name)
            self.ms_url = line_edit("", placeholder="https://server/{z}/{x}/{y}.png")
            form.add_field("url_template", t("URL template"), self.ms_url)
            self.ms_attribution = line_edit("")
            form.add_field("attribution", t("Attribution"), self.ms_attribution, span=2)
            self.ms_min_zoom = spin(0, 0, 30)
            form.add_field("min_zoom", t("Min zoom"), self.ms_min_zoom)
            self.ms_max_zoom = spin(19, 0, 30)
            form.add_field("max_zoom", t("Max zoom"), self.ms_max_zoom)
            # {s} and {apiKey} in the URL template (e.g. CARTO needs a free key of its own).
            self.ms_subdomains = line_edit("", placeholder="a,b,c")
            form.add_field("subdomains", t("Subdomains"), self.ms_subdomains)
            self.ms_api_key = line_edit("")
            self.ms_api_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            form.add_field("api_key", t("API key"), self.ms_api_key)
            self.ms_notes = text_edit("", height=56)
            form.add_field("notes", t("Notes"), self.ms_notes, span=2)
            self.ms_enabled = checkbox(t("Enabled"), True)
            form.add_widget(self.ms_enabled)
            self.ms_cache = checkbox(t("Enable local cache/proxy"))
            form.add_widget(self.ms_cache)
            self.ms_default = checkbox(t("Set as default"))
            form.add_widget(self.ms_default)
            self.map_form_title = label(t("Add map source"), "h3")
            card.add(self.map_form_title)
            card.add(form)
            buttons = QHBoxLayout()
            buttons.addWidget(button(t("Save map source"), icon_name="content-save-outline", on_click=self._save_map_source))
            self.ms_cancel = button(t("Cancel"), variant="secondary", on_click=self._new_map_source)
            buttons.addWidget(self.ms_cancel)
            buttons.addStretch(1)
            card.add(buttons)
        else:
            card.add(label(t("Your role can view map source configuration but cannot change it."), "muted"))

    def _build_database(self) -> None:
        card, self.db_flash = self._section(t("Database maintenance"), "application/settings_database")
        self.db_warning = label(t("Database integrity check returned a non-OK result. Review storage before running maintenance actions."),
                                "error", wrap=True)
        self.db_warning.hide()
        card.add(self.db_warning)
        self.db_info = QVBoxLayout()
        card.add(self.db_info)
        self.db_table = DataTable([Column("label", t("Runtime table"), stretch=True), Column("row_count", t("Rows"), width=100)],
                                  sortable=False)
        self.db_table.setMinimumHeight(200)
        card.add(self.db_table)
        self.db_total = label("", "muted")
        card.add(self.db_total)
        card.add(label(t("Runtime reset clears only operational history tables. Configuration tables (TNC, DIGI flows, station/WX settings, objects/items/bulletins, map sources, users) and APRS message history are not modified."),
                       "muted", wrap=True))
        if self.manage:
            actions = QHBoxLayout()
            self.vacuum_button = button(t("Run VACUUM"), icon_name="database-refresh-outline", on_click=self._vacuum)
            self.reset_button = button(t("Reset runtime logs/data"), variant="secondary", icon_name="delete-sweep-outline",
                                       on_click=self._reset_runtime_data)
            actions.addWidget(self.vacuum_button)
            actions.addWidget(self.reset_button)
            actions.addStretch(1)
            card.add(actions)
        else:
            card.add(label(t("Your role can view database maintenance status but cannot start maintenance actions."), "muted"))

    def _build_danger_zone(self) -> None:
        card, self.danger_flash = self._section(t("Danger zone"), "application/settings_danger_zone")
        if not self.manage:
            return
        rows = QGridLayout()
        rows.setHorizontalSpacing(14)
        rows.setVerticalSpacing(10)
        items = [
            (t("Restart services"), t("Restarts the APRS runtime (interfaces, APRS-IS, schedulers) inside this window."),
             button(t("Restart services"), variant="secondary", icon_name="restart", on_click=self._restart_runtime)),
            (t("Reboot host"), t("Restarts this computer."),
             button(t("Reboot host"), variant="danger", icon_name="restart-alert", on_click=lambda: self._host_power("reboot"))),
            (t("Power off host"), t("Shuts down this computer."),
             button(t("Power off host"), variant="danger", icon_name="power", on_click=lambda: self._host_power("poweroff"))),
        ]
        for row, (title, description, widget) in enumerate(items):
            text = QVBoxLayout()
            text.addWidget(label(title, "h3"))
            text.addWidget(label(description, "small", wrap=True))
            rows.addLayout(text, row, 0)
            rows.addWidget(widget, row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rows.setColumnStretch(0, 1)
        card.add(rows)

    # =============================================================== loading
    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import (
            RUNTIME_MAINTENANCE_RESET_TABLES,
            VACUUM_RECOMMEND_FREE_BYTES_MIN,
            VACUUM_RECOMMEND_FREE_RATIO_MIN,
            database_maintenance_snapshot,
            get_app_setting,
            get_event_log_debug_enabled,
            get_event_log_min_level,
            get_traffic_retention_minutes,
        )
        from app.i18n import get_app_language
        from app.services.alarm_groups import (
            build_automatic_aprsis_alarm_filter,
            get_aprs_alarm_category_thresholds,
            get_aprs_alarm_enabled,
            get_aprs_alarm_groups,
        )
        from app.services.aprs_device_identification import get_aprs_device_identification_status
        from app.services.content import get_aprs_symbol_set, get_station_settings, has_enabled_modem_interface
        from app.desktop.remote_receivers import remote_url
        from app.desktop.tiles import fast_map_enabled, tile_cache_limit_mb, tile_cache_size_bytes
        from app.services.cwop import get_cwop_settings, get_cwop_status
        from app.services.map_service import (
            get_coverage_fill_opacity_percent,
            get_map_marker_clustering_enabled,
            get_map_marker_spiderfy_enabled,
            get_map_marker_spiderfy_nearby_distance_px,
            get_map_marker_spiderfy_zoom_levels,
            list_map_sources,
        )
        from app.services.messages import get_effective_message_target_groups
        from app.services.system import current_gui_version
        from app.ui_palette import normalize_ui_palette

        snapshot = database_maintenance_snapshot()
        counts = dict(snapshot.get("tracked_row_counts") or {})
        reset_targets = [
            {"table_name": table, "label": DATABASE_TABLE_LABELS.get(table, table), "row_count": int(counts.get(table) or 0)}
            for table in RUNTIME_MAINTENANCE_RESET_TABLES if table in counts
        ]
        groups = get_aprs_alarm_groups()
        min_level = str(get_event_log_min_level() or "INFO").upper()
        return {
            "language": get_app_language(),
            "units": get_station_settings(include_tx_runtime=False).get("default_units", "metric"),
            "retention": int(get_traffic_retention_minutes()),
            "symbol_set": get_aprs_symbol_set(),
            "palette": normalize_ui_palette(get_app_setting("ui_palette")),
            "log_min_level": min_level if min_level in EVENT_LOG_MIN_LEVEL_OPTIONS else "INFO",
            "log_debug": bool(get_event_log_debug_enabled()),
            "coverage_fill": int(get_coverage_fill_opacity_percent()),
            "clustering": bool(get_map_marker_clustering_enabled()),
            "spiderfy": bool(get_map_marker_spiderfy_enabled()),
            "spiderfy_levels": int(get_map_marker_spiderfy_zoom_levels()),
            "spiderfy_distance": int(get_map_marker_spiderfy_nearby_distance_px()),
            "version": current_gui_version(),
            "device_identification": get_aprs_device_identification_status(),
            "alarm_enabled": bool(get_aprs_alarm_enabled()),
            "alarm_groups": groups,
            "alarm_thresholds": get_aprs_alarm_category_thresholds(),
            "rf_groups": get_effective_message_target_groups(alarm_groups=groups, source_kind="rf"),
            "aprsis_filter": build_automatic_aprsis_alarm_filter(groups),
            "map_sources": [{**source, "order": index} for index, source in enumerate(list_map_sources(), start=1)],
            "db": snapshot,
            "db_blocked": bool(has_enabled_modem_interface()),
            "db_reset_targets": reset_targets,
            "vacuum_threshold": (VACUUM_RECOMMEND_FREE_BYTES_MIN, VACUUM_RECOMMEND_FREE_RATIO_MIN),
            "cwop": get_cwop_settings(),
            "cwop_status": get_cwop_status(),
            "tile_cache_limit_mb": tile_cache_limit_mb(),
            "fast_map": fast_map_enabled(),
            "tile_cache_bytes": tile_cache_size_bytes(),
            "remote_receiver_url": remote_url(),
        }

    def _apply(self, view: dict[str, Any]) -> None:
        self.view = view or {}
        if not self._loaded_forms:
            self._loaded_forms = True
            self._fill_forms()
        self._render_version()
        self._render_cwop_status()
        self._render_device_identification()
        self._render_alarm_info()
        self.map_table.set_rows(list(self.view.get("map_sources") or []))
        self.map_actions.sync()
        self._render_database()

    def _fill_forms(self) -> None:
        view = self.view
        set_combo_value(self.language, view.get("language"))
        set_combo_value(self.units, view.get("units") or "metric")
        set_combo_value(self.retention, view.get("retention"))
        set_combo_value(self.symbol_set, view.get("symbol_set") or "legacy")
        set_combo_value(self.log_level, view.get("log_min_level") or "INFO")
        self.debug_logs.setChecked(bool(view.get("log_debug")))
        radio = self.palette_buttons.get(str(view.get("palette") or ""))
        if radio is not None:
            radio.setChecked(True)
        set_combo_value(self.coverage_fill, view.get("coverage_fill"))
        try:
            outline = int(self.context.ui_value("map/coverage_outline_opacity", 100))
        except (TypeError, ValueError):
            outline = 100
        set_combo_value(self.coverage_outline, outline)
        self.clustering.setChecked(bool(view.get("clustering")))
        self.spiderfy.setChecked(bool(view.get("spiderfy")))
        self.spiderfy_levels.setValue(int(view.get("spiderfy_levels") or 0))
        self.spiderfy_distance.setValue(int(view.get("spiderfy_distance") or 1))
        from app.desktop import autostart

        self.require_login.setChecked(ctx.require_login())
        self.minimize_tray.setChecked(ctx.minimize_to_tray())
        self.start_minimized.setChecked(ctx.start_minimized())
        self.alert_sound.setChecked(ctx.alert_sound_enabled())
        self.station_icon.setChecked(ctx.station_symbol_icon_enabled())
        self.autostart.setChecked(autostart.is_enabled())
        set_combo_value(self.tile_cache_limit, view.get("tile_cache_limit_mb"))
        self.fast_map.setChecked(bool(view.get("fast_map")))
        self.remote_receiver.setText(str(view.get("remote_receiver_url") or ""))
        self._render_tile_cache_used(int(view.get("tile_cache_bytes") or 0))
        cwop = view.get("cwop") or {}
        self.cwop_enabled.setChecked(bool(cwop.get("enabled")))
        self.alarm_enabled.setChecked(bool(view.get("alarm_enabled")))
        self.alarm_groups.setText(", ".join(view.get("alarm_groups") or []))
        thresholds = view.get("alarm_thresholds") or {}
        for key, (alerts, popup) in self.threshold_rows.items():
            values = thresholds.get(key) or {}
            set_combo_value(alerts, str(values.get("alerts")) if int(values.get("alerts") or 0) > 0 else "off")
            set_combo_value(popup, str(values.get("popup")) if int(values.get("popup") or 0) > 0 else "off")
        if self.manage:
            self._new_map_source()

    def _render_version(self) -> None:
        from app.desktop.widgets.common import clear_layout

        clear_layout(self.version_info)
        self.version_info.addWidget(key_value_grid([
            (t("Current version"), self.view.get("version") or "-"),
            (t("Program folder"), str(Path(__file__).resolve().parents[3])),
            (t("Python"), sys.version.split()[0]),
        ]))

    def _render_device_identification(self) -> None:
        from app.desktop.widgets.common import clear_layout

        status = self.view.get("device_identification") or {}
        rows = [
            (t("Status"), t(status.get("status_label") or "")),
            (t("Active source"), t(status.get("active_source_label") or "")),
            (t("Generation time"), format_display_datetime(status.get("generation_time"), use_utc=True) or t("Unknown")),
            (t("Last successful update"), format_display_datetime(status.get("last_success_at"), use_utc=True) or t("Never")),
        ]
        if status.get("last_error"):
            rows.append((t("Last update error"), status.get("last_error")))
        clear_layout(self.device_info)
        self.device_info.addWidget(key_value_grid(rows))

    def _render_alarm_info(self) -> None:
        from app.desktop.widgets.common import clear_layout

        clear_layout(self.alarm_info)
        groups = self.view.get("alarm_groups") or []
        self.alarm_info.addWidget(key_value_grid([
            (t("APRS alarms"), t("Enabled") if self.view.get("alarm_enabled") else t("Disabled")),
            (t("Alarm groups"), ", ".join(groups) if groups else t("none")),
            (t("RF receive groups"), ", ".join(self.view.get("rf_groups") or [])),
            (t("Automatic APRS-IS filter"), self.view.get("aprsis_filter") or t("none")),
        ]))

    def _render_database(self) -> None:
        from app.desktop.widgets.common import clear_layout

        snapshot = self.view.get("db") or {}
        quick_check = str(snapshot.get("quick_check") or "unknown").strip()
        ok = quick_check.lower() == "ok"
        blocked = bool(self.view.get("db_blocked"))
        recommended = bool(snapshot.get("vacuum_recommended"))
        if not ok:
            recommendation = "Integrity check returned issues. Investigate before maintenance operations."
        elif recommended and blocked:
            recommendation = "Recommended now based on reclaimable space, but blocked while any TNC is enabled."
        elif recommended:
            recommendation = "Recommended now based on reclaimable space."
        else:
            recommendation = "Not required now; reclaimable space is below the recommendation threshold."
        threshold_bytes, threshold_ratio = self.view.get("vacuum_threshold") or (0, 0)
        from app.db import DEFAULT_EVENT_LOG_KEEP_ROWS, DEFAULT_EVENT_LOG_WARNING_DAYS

        self.db_warning.setVisible(not ok)
        clear_layout(self.db_info)
        self.db_info.addWidget(key_value_grid([
            (t("Database path"), snapshot.get("database_path") or "-"),
            (t("Database file size"), format_size_bytes(snapshot.get("database_file_bytes"))),
            (t("WAL file size"), format_size_bytes(snapshot.get("wal_file_bytes"))),
            (t("SHM file size"), format_size_bytes(snapshot.get("shm_file_bytes"))),
            (t("Allocated database size"), format_size_bytes(snapshot.get("allocated_bytes"))),
            (t("Reclaimable space"), f"{format_size_bytes(snapshot.get('reclaimable_bytes'))} ({format_ratio_percent(snapshot.get('reclaimable_ratio'))})"),
            (t("Page geometry"), tf("page_size={size} B, page_count={count}, freelist_count={free}", size=snapshot.get("page_size") or 0,
                                   count=snapshot.get("page_count") or 0, free=snapshot.get("freelist_count") or 0)),
            (t("Integrity check"), quick_check),
            (t("Automatic pruning"), t("Enabled")),
            (t("Policy"), tf("Keep newest {count} rows in event logs after midnight; warnings and errors for {days} days.",
                             count=DEFAULT_EVENT_LOG_KEEP_ROWS, days=DEFAULT_EVENT_LOG_WARNING_DAYS)),
            (t("VACUUM recommendation"), t(recommendation)),
            (t("Recommendation threshold"), tf("At least {size} reclaimable and {ratio} free-page ratio.",
                                                size=format_size_bytes(threshold_bytes), ratio=format_ratio_percent(threshold_ratio))),
            (t("VACUUM availability"), t("Disabled while any TNC is enabled.") if blocked else t("Available when all TNC interfaces are disabled.")),
        ]))
        targets = [{**target, "label": t(target["label"])} for target in self.view.get("db_reset_targets") or []]
        self.db_table.set_row_key(lambda row: row.get("table_name"))
        self.db_table.set_rows(targets)
        self.db_total.setText(tf("Rows selected for runtime reset: {count}.", count=sum(int(row["row_count"]) for row in targets)))
        if self.manage:
            for widget, message in ((self.vacuum_button, "Disable all TNC interfaces before running database vacuum."),
                                    (self.reset_button, "Disable all TNC interfaces before clearing runtime logs and traffic history.")):
                widget.setEnabled(not blocked)
                widget.setToolTip(t(message) if blocked else "")

    # ================================================================= global
    def _save_global(self) -> None:
        from app.db import (
            EVENT_LOG_DEBUG_ENABLED_SETTING_KEY,
            EVENT_LOG_MIN_LEVEL_SETTING_KEY,
            TRAFFIC_RETENTION_MINUTES_SETTING_KEY,
            set_app_setting,
        )
        from app.desktop.i18n import current_language, set_language
        from app.services.content import get_station_settings, safe_update_station_settings
        from app.services.map_service import (
            COVERAGE_FILL_OPACITY_SETTING_KEY,
            MAP_MARKER_SPIDERFY_ENABLED_SETTING_KEY,
            MAP_MARKER_SPIDERFY_NEARBY_DISTANCE_SETTING_KEY,
            MAP_MARKER_SPIDERFY_ZOOM_LEVELS_SETTING_KEY,
            save_map_marker_clustering_enabled,
        )
        from app.services.content import APRS_SYMBOL_SET_SETTING_KEY

        station = dict(get_station_settings())
        station["default_units"] = str(combo_value(self.units) or "metric")
        success, error = safe_update_station_settings(station)
        if not success:
            self.global_flash.error(t(str(error or "Failed to update global settings.")))
            return
        palette = next((name for name, radio in self.palette_buttons.items() if radio.isChecked()), None)
        language = str(combo_value(self.language) or "en")
        set_app_setting("app_language", language)
        set_app_setting(TRAFFIC_RETENTION_MINUTES_SETTING_KEY, str(combo_value(self.retention)))
        previous_palette = str(self.view.get("palette") or "")
        if palette:
            set_app_setting("ui_palette", palette)
        set_app_setting(APRS_SYMBOL_SET_SETTING_KEY, str(combo_value(self.symbol_set) or "legacy"))
        set_app_setting(EVENT_LOG_MIN_LEVEL_SETTING_KEY, str(combo_value(self.log_level) or "INFO"))
        set_app_setting(EVENT_LOG_DEBUG_ENABLED_SETTING_KEY, "1" if self.debug_logs.isChecked() else "0")
        set_app_setting(COVERAGE_FILL_OPACITY_SETTING_KEY, str(combo_value(self.coverage_fill)))
        save_map_marker_clustering_enabled(self.clustering.isChecked())
        set_app_setting(MAP_MARKER_SPIDERFY_ENABLED_SETTING_KEY, "1" if self.spiderfy.isChecked() else "0")
        set_app_setting(MAP_MARKER_SPIDERFY_ZOOM_LEVELS_SETTING_KEY, str(self.spiderfy_levels.value()))
        set_app_setting(MAP_MARKER_SPIDERFY_NEARBY_DISTANCE_SETTING_KEY, str(self.spiderfy_distance.value()))
        self.context.set_ui_value("map/coverage_outline_opacity", int(combo_value(self.coverage_outline) or 0))
        language_changed = language != current_language()
        palette_changed = bool(palette) and palette != previous_palette
        if language_changed:
            set_language(language)
        if palette_changed:
            from app.desktop.app import apply_current_theme

            apply_current_theme(self.context)
        # Every page is rebuilt so texts, units, icons and colours follow the new settings.
        self.context.signals.settings_changed.emit()
        window = self.context.main_window
        page = window.pages.get("settings") if window is not None else None
        target = page if isinstance(page, SettingsPage) else self
        target.global_flash.success(t("Global settings updated."))

    # ================================================================ desktop
    def _save_desktop(self) -> None:
        from app.desktop import autostart

        if self.require_login.isChecked() and not ctx.require_login():
            from app.db import fetch_one

            row = fetch_one("SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND is_active = 1")
            if row is None or int(row["total"] or 0) == 0:
                self.desktop_flash.error(t("Create an active admin account in Users / Roles before requiring sign-in."))
                return
        ctx.set_require_login(self.require_login.isChecked())
        ctx.set_minimize_to_tray(self.minimize_tray.isChecked())
        ctx.set_start_minimized(self.start_minimized.isChecked())
        ctx.set_alert_sound_enabled(self.alert_sound.isChecked())
        ctx.set_station_symbol_icon_enabled(self.station_icon.isChecked())
        from app.desktop.remote_receivers import set_remote_url
        from app.desktop.tiles import set_fast_map, set_tile_cache_limit_mb

        try:
            self.remote_receiver.setText(set_remote_url(self.remote_receiver.text()))
        except ValueError as exc:
            self.desktop_flash.error(t(str(exc)))
            return
        set_tile_cache_limit_mb(int(combo_value(self.tile_cache_limit)))  # a lower limit trims right away
        set_fast_map(self.fast_map.isChecked())  # takes effect at once
        self.context.notify_data_changed("station")  # refreshes the window and tray icon
        if autostart.is_supported() and self.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError as exc:
                self.desktop_flash.error(f"{t('Could not change Windows autostart.')} {exc}")
                return
        self.desktop_flash.success(t("Settings saved."))

    def _render_tile_cache_used(self, used_bytes: int) -> None:
        limit_mb = int(combo_value(self.tile_cache_limit) or 0)
        self.tile_cache_used.setText(tf("Now on disk: {used} of {limit}", used=format_megabytes(used_bytes / 2**20),
                                        limit=format_megabytes(limit_mb)))

    # ================================================================ version
    def _check_version(self) -> None:
        from app.services.system import latest_gui_version

        self.check_version_button.setEnabled(False)
        self.version_flash.info(t("Checking..."))

        def done(result: dict[str, Any]) -> None:
            self.check_version_button.setEnabled(True)
            if not result.get("ok"):
                self.version_flash.error(t(str(result.get("error") or "Version check failed.")))
                return
            text = tf("Latest upstream version: {latest} (installed: {current}).", latest=result.get("latest_version"),
                      current=result.get("current_version"))
            if result.get("up_to_date"):
                self.version_flash.success(f"{text} {t('Up to date.')}")
            else:
                self.version_flash.info(f"{text} {t('A newer version is available upstream.')}")

        def failed(error: BaseException) -> None:
            self.check_version_button.setEnabled(True)
            self.version_flash.error(str(error))

        run_async(latest_gui_version, done, failed, owner=self)

    # ===================================================== device identification
    def _update_device_identification(self) -> None:
        from app.services.aprs_device_identification import refresh_aprs_device_identification_cache

        self.device_flash.info(t("Working..."))

        def done(result: dict[str, Any]) -> None:
            if result.get("ok"):
                self.device_flash.success(t("APRS device identification database updated."))
            else:
                self.device_flash.error(t(str(result.get("error") or "APRS device identification database update failed.")))
            self.refresh()

        run_async(refresh_aprs_device_identification_cache, done, lambda exc: self.device_flash.error(str(exc)), owner=self)

    # ================================================================= backup
    def _export_backup(self) -> None:
        from app.services.config_backup import build_configuration_backup_filename, export_configuration_backup_bytes

        default_name = build_configuration_backup_filename()
        start = str(Path.home() / "Documents" / default_name)
        path, _filter = QFileDialog.getSaveFileName(self, t("Export configuration backup"), start, "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_bytes(export_configuration_backup_bytes())
        except OSError as exc:
            self.backup_flash.error(f"{t('Failed to export configuration backup.')} {exc}")
            return
        self.backup_flash.success(tf("Configuration backup saved to {path}.", path=path))

    def _import_backup(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, t("Import configuration backup"), str(Path.home() / "Documents"),
                                                    "JSON (*.json)")
        if not path:
            return
        if not confirm(self, t("Are you sure?"), t("Import configuration backup now? Current settings will be replaced."), danger=True,
                       confirm_text=t("Import configuration backup")):
            return
        try:
            payload = Path(path).read_bytes()[: CONFIG_BACKUP_MAX_BYTES + 1]
        except OSError as exc:
            self.backup_flash.error(str(exc))
            return
        if not payload:
            self.backup_flash.error(t("Backup file is empty."))
            return
        if len(payload) > CONFIG_BACKUP_MAX_BYTES:
            self.backup_flash.error(t("Backup file is too large (limit: 5 MB)."))
            return
        from app.services.config_backup import safe_import_configuration_backup

        success, error = safe_import_configuration_backup(payload)
        if not success:
            self.backup_flash.error(t(error or "Failed to import configuration backup."))
            return
        self.backup_flash.success(t("Configuration backup imported. Restart services to apply runtime changes."))
        self._loaded_forms = False
        self.refresh()

    # ================================================================= alarms
    def _save_alarms(self) -> None:
        from app.services.alarm_groups import (
            normalize_aprs_alarm_category_thresholds,
            normalize_aprs_alarm_groups,
            save_aprs_alarm_category_thresholds,
            save_aprs_alarm_enabled,
            save_aprs_alarm_groups,
        )
        from app.services.messages import reconcile_effective_message_group_conversations

        try:
            groups = normalize_aprs_alarm_groups(self.alarm_groups.text())
            current = dict(self.view.get("alarm_thresholds") or {})
            thresholds = normalize_aprs_alarm_category_thresholds({
                key: {
                    "alerts": str(combo_value(alerts) or "off"),
                    "map": (current.get(key) or {}).get("map"),
                    "popup": str(combo_value(popup) or "off"),
                }
                for key, (alerts, popup) in self.threshold_rows.items()
            })
            save_aprs_alarm_enabled(self.alarm_enabled.isChecked())
            saved_groups = save_aprs_alarm_groups(groups)
            save_aprs_alarm_category_thresholds(thresholds)
            reconcile_effective_message_group_conversations(alarm_groups=saved_groups)
        except ValueError as exc:
            self.alarm_flash.error(t(str(exc)))
            return
        self.alarm_flash.success(t("APRS alarm settings updated."))
        self.context.notify_data_changed("alerts")
        self._loaded_forms = False
        self.refresh()

    # ============================================================ map sources
    def _fill_map_source(self, source: dict[str, Any] | None) -> None:
        source = source or {}
        self.map_source_id = int(source["id"]) if source.get("id") else None
        self.map_form_title.setText(t("Edit map source") if self.map_source_id else t("Add map source"))
        self.ms_name.setText(str(source.get("name") or ""))
        self.ms_url.setText(str(source.get("url_template") or ""))
        self.ms_attribution.setText(str(source.get("attribution") or ""))
        self.ms_min_zoom.setValue(int(source.get("min_zoom") or 0))
        self.ms_max_zoom.setValue(int(source.get("max_zoom") if source.get("max_zoom") is not None else 19))
        self.ms_subdomains.setText(str(source.get("subdomains") or ""))
        self.ms_api_key.setText(str(source.get("api_key") or ""))
        self.ms_notes.setPlainText(str(source.get("notes") or ""))
        self.ms_enabled.setChecked(bool(source.get("enabled", True)))
        self.ms_cache.setChecked(bool(source.get("local_cache_enabled")))
        self.ms_default.setChecked(bool(source.get("is_default")))
        self.ms_cancel.setVisible(self.map_source_id is not None)

    def _new_map_source(self) -> None:
        self._fill_map_source({"enabled": True, "min_zoom": 0, "max_zoom": 19})

    def _edit_map_source(self, row: dict[str, Any]) -> None:
        from app.services.map_service import get_map_source

        self._fill_map_source(get_map_source(int(row["id"])))

    def _map_changed(self, message: str) -> None:
        from app.desktop.tiles import shared_tile_manager

        self.map_flash.success(t(message))
        shared_tile_manager().reload_source()
        self.context.notify_data_changed("map-sources")
        self.refresh()

    def _save_map_source(self) -> None:
        from app.services.map_service import safe_save_map_source

        payload = {
            "name": self.ms_name.text().strip(),
            "url_template": self.ms_url.text().strip(),
            "attribution": self.ms_attribution.text().strip(),
            "min_zoom": str(self.ms_min_zoom.value()),
            "max_zoom": str(self.ms_max_zoom.value()),
            "subdomains": self.ms_subdomains.text().strip(),
            "api_key": self.ms_api_key.text().strip(),
            "enabled": "1" if self.ms_enabled.isChecked() else None,
            "local_cache_enabled": "1" if self.ms_cache.isChecked() else None,
            "is_default": "1" if self.ms_default.isChecked() else None,
            "notes": self.ms_notes.toPlainText().strip(),
        }
        success, error, _saved = safe_save_map_source(payload, source_id=self.map_source_id)
        if not success:
            self.map_flash.error(t(error or "Failed to save map source."))
            return
        self._new_map_source()
        self._map_changed("Map source saved.")

    def _default_map_source(self, row: dict[str, Any]) -> None:
        from app.services.map_service import safe_set_default_map_source

        success, error = safe_set_default_map_source(int(row["id"]))
        if not success:
            self.map_flash.error(t(error or "Failed to change default map source."))
            return
        self._map_changed("Default map source updated.")

    def _move_map_source(self, row: dict[str, Any], direction: str) -> None:
        from app.services.map_service import safe_move_map_source

        success, error = safe_move_map_source(int(row["id"]), direction)
        if not success:
            self.map_flash.error(t(error or "Failed to reorder map source."))
            return
        self._map_changed("Map source order updated.")

    def _delete_map_source(self, row: dict[str, Any]) -> None:
        if not confirm(self, t("Are you sure?"), t("Delete this map source?") + f"\n\n{row.get('name')}", danger=True,
                       confirm_text=t("Delete")):
            return
        from app.services.map_service import safe_delete_map_source

        success, error = safe_delete_map_source(int(row["id"]))
        if not success:
            self.map_flash.error(t(error or "Failed to delete map source."))
            return
        if self.map_source_id == int(row["id"]):
            self._new_map_source()
        self._map_changed("Map source deleted.")

    def _clear_map_cache(self, row: dict[str, Any]) -> None:
        from app.services.map_tile_proxy import safe_clear_map_source_cache

        success, error = safe_clear_map_source_cache(int(row["id"]))
        if not success:
            self.map_flash.error(t(error or "Failed to clear map source cache."))
            return
        from app.desktop.tiles import shared_tile_manager

        shared_tile_manager().clear_memory()
        self._map_changed("Map source cache cleared.")

    # =============================================================== database
    def _vacuum(self) -> None:
        if not confirm(self, t("Are you sure?"), t("Run database vacuum now?"), confirm_text=t("Run VACUUM")):
            return
        from app.db import vacuum_database
        from app.services.content import has_enabled_modem_interface

        if has_enabled_modem_interface():
            self.db_flash.error(t("Disable all TNC interfaces before running database vacuum."))
            return
        self.db_flash.info(t("Working..."))

        def done(_result: Any) -> None:
            self.db_flash.success(t("Database vacuum completed."))
            self.refresh()

        run_async(vacuum_database, done, lambda exc: self.db_flash.error(str(exc)), owner=self)

    def _reset_runtime_data(self) -> None:
        if not confirm(self, t("Are you sure?"), t("Clear runtime logs and traffic history now? Configuration tables will not be changed."),
                       danger=True, confirm_text=t("Reset runtime logs/data")):
            return
        from app.db import reset_runtime_operational_data
        from app.services.content import has_enabled_modem_interface

        if has_enabled_modem_interface():
            self.db_flash.error(t("Disable all TNC interfaces before clearing runtime logs and traffic history."))
            return

        def done(deleted: dict[str, int]) -> None:
            total = sum(int(value) for value in (deleted or {}).values())
            self.db_flash.success(f"{t('Runtime logs and traffic history cleared.')} ({total})")
            self.context.notify_data_changed("traffic")
            self.refresh()

        run_async(reset_runtime_operational_data, done, lambda exc: self.db_flash.error(str(exc)), owner=self)

    # ============================================================ danger zone
    def _restart_runtime(self) -> None:
        if not confirm(self, t("Are you sure?"), t("Restart services now?"), confirm_text=t("Restart services")):
            return
        runtime = getattr(self.context, "runtime", None)
        if runtime is None:
            self.danger_flash.error(t("APRS runtime is not running."))
            return
        self.danger_flash.info(t("Restarting..."))

        def done(_result: Any) -> None:
            self.danger_flash.success(t("Services restarted."))
            self.context.signals.runtime_restarted.emit()

        run_async(runtime.restart, done, lambda exc: self.danger_flash.error(str(exc)), owner=self)

    def _host_power(self, action: str) -> None:
        question = t("Reboot host now?") if action == "reboot" else t("Power off host now?")
        if not confirm(self, t("Are you sure?"), question, danger=True,
                       confirm_text=t("Reboot host") if action == "reboot" else t("Power off host")):
            return
        if sys.platform != "win32":
            self.danger_flash.error(t("This action is available only on Windows."))
            return
        arguments = ["shutdown", "/r" if action == "reboot" else "/s", "/t", "10", "/c", "APRSBox"]
        try:
            subprocess.run(arguments, check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.CalledProcessError) as exc:
            self.danger_flash.error(f"{t('Operation failed.')} {exc}")
            return
        self.danger_flash.success(t("The computer will restart in 10 seconds.") if action == "reboot"
                                  else t("The computer will shut down in 10 seconds."))
