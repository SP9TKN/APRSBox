"""My Station page (web: templates/station.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.tasks import SerialLoader
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
    form_flag,
    icon_button,
    label,
    line_edit,
    set_combo_options,
    set_combo_value,
    truthy,
)
from app.desktop.widgets.pickers import CharCounterLabel, LocationPickerDialog, PhgGeneratorDialog, SymbolPicker, insert_phg
from app.desktop.widgets.table import Column, DataTable

BEACON_COMMENT_MAX = 43
STATUS_TEXT_MAX = 62
INTERVAL_OPTIONS = ((15, "15m"), (30, "30m"), (45, "45m"), (60, "60m"))
HEALTH_TONES = {"ok": "success", "warning": "warning", "not_recommended": "error", "not-recommended": "error", "neutral": "info"}


def station_form_options() -> dict[str, list[dict[str, Any]]]:
    """Port of pages._station_form_options (interface choices)."""
    from app.services.content import get_active_tnc_interfaces
    from app.services.tx_scope import ALL_ACTIVE_INTERFACE_OPTION_VALUE, INTERNAL_TX_INTERFACE_OPTION_VALUE

    options = [{"value": "", "label": "Select interface"}]
    for item in get_active_tnc_interfaces():
        options.append({"value": str(item["id"]), "label": f"{item['name']} ({item['modem_type']}, {item['band'] or '-'})"})
    options.append({"value": ALL_ACTIVE_INTERFACE_OPTION_VALUE, "label": "Transmit on all active interfaces"})
    options.append({"value": INTERNAL_TX_INTERFACE_OPTION_VALUE, "label": "Internal TX"})
    return {"interface_options": options}


def selected_interface_value(station: dict[str, Any]) -> str:
    from app.services.tx_scope import ALL_ACTIVE_INTERFACE_OPTION_VALUE, INTERNAL_TX_INTERFACE_OPTION_VALUE

    if station.get("beacon_internal_tx"):
        return INTERNAL_TX_INTERFACE_OPTION_VALUE
    if station.get("beacon_tx_scope") == "all_active":
        return ALL_ACTIVE_INTERFACE_OPTION_VALUE
    value = station.get("beacon_interface_id")
    return str(value) if value is not None else ""


class StationPage(Page):
    page_key = "station"

    def build(self) -> None:
        self.station: dict[str, Any] = {}
        card = Card(t("Position Beacon"))
        card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/station")))
        card.add(self.flash)
        grid = FormGrid(2)
        self.callsign = line_edit("", max_length=6)
        self.callsign.textEdited.connect(lambda text: self._uppercase(self.callsign))
        self.ssid = combo([("", "Select SSID")] + [(str(value), str(value)) for value in range(16)], "")
        self.interface = QComboBox()
        self.interface.currentIndexChanged.connect(lambda _index: self._sync_internal_notice())
        grid.add_field("callsign", t("Callsign"), self.callsign)
        grid.add_field("ssid", t("SSID"), self.ssid)
        grid.add_field("beacon_interface_id", t("Interface"), self.interface)
        self.internal_tx_notice = FlashMessage()
        grid.add_widget(self.internal_tx_notice)
        comment_row = QWidget()
        comment_layout = QHBoxLayout(comment_row)
        comment_layout.setContentsMargins(0, 0, 0, 0)
        self.comment = line_edit("", max_length=BEACON_COMMENT_MAX)
        self.comment_counter = CharCounterLabel(BEACON_COMMENT_MAX)
        self.comment.textChanged.connect(self.comment_counter.update_count)
        comment_layout.addWidget(self.comment, 1)
        comment_layout.addWidget(self.comment_counter)
        comment_layout.addWidget(icon_button("calculator", t("Generate PHG"), self._open_phg))
        grid.add_field("beacon_comment", t("Beacon Comment"), comment_row)
        self.interval = combo([(str(value), caption) for value, caption in INTERVAL_OPTIONS] + [("proportional", "Proportional Path")], "30")
        self.interval.currentIndexChanged.connect(self._render_health)
        grid.add_field("beacon_interval_minutes", t("Beacon at every"), self.interval)
        self.path = line_edit("")
        self.path.textEdited.connect(lambda text: self._uppercase(self.path))
        self.path.textChanged.connect(self._render_health)
        grid.add_field("beacon_path", t("Beacon Path"), self.path)
        self.health = FlashMessage()
        grid.add_widget(self.health)
        self.schedule_title = label("", "h3")
        self.schedule = QPlainTextEdit()
        self.schedule.setReadOnly(True)
        self.schedule.setProperty("role", "log")
        self.schedule.setFixedHeight(150)
        schedule_box = QWidget()
        schedule_layout = QVBoxLayout(schedule_box)
        schedule_layout.setContentsMargins(0, 0, 0, 0)
        schedule_layout.addWidget(self.schedule_title)
        schedule_layout.addWidget(self.schedule)
        self.schedule_box = schedule_box
        grid.add_widget(schedule_box)
        location_button = button(t("Get location"), variant="secondary", icon_name="map-marker-plus-outline", on_click=self._pick_location)
        grid.add_widget(location_button)
        self.latitude = line_edit("")
        self.longitude = line_edit("")
        grid.add_field("latitude", t("Latitude"), self.latitude)
        grid.add_field("longitude", t("Longitude"), self.longitude)
        self.symbol = SymbolPicker()
        grid.add_widget(self.symbol)
        self.tx_enabled = checkbox(t("Enable beacon transmission"))
        grid.add_widget(self.tx_enabled)
        card.add(grid)
        self.body.addWidget(card)

        status_card = Card(t("APRS Status"))
        status_grid = FormGrid(2)
        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.status_text = line_edit("", placeholder=t("Station online"), max_length=STATUS_TEXT_MAX)
        self.status_counter = CharCounterLabel(STATUS_TEXT_MAX)
        self.status_text.textChanged.connect(self.status_counter.update_count)
        status_layout.addWidget(self.status_text, 1)
        status_layout.addWidget(self.status_counter)
        status_grid.add_field("status_text", t("Status Text"), status_row)
        self.status_interval = combo([(str(value), caption) for value, caption in INTERVAL_OPTIONS], "30")
        status_grid.add_field("status_interval_minutes", t("APRS Status at every"), self.status_interval)
        self.status_enabled = checkbox(t("Enable APRS Status transmission"))
        status_grid.add_widget(self.status_enabled)
        status_card.add(status_grid)
        actions = QHBoxLayout()
        self.save_button = button(t("Save Station Settings"), icon_name="content-save-outline", on_click=self.save)
        self.beacon_button = button(t("Send beacon"), variant="secondary", icon_name="satellite-uplink", on_click=self.send_beacon)
        self.status_button = button(t("Send status"), variant="secondary", icon_name="message-text-outline", on_click=self.send_status)
        actions.addWidget(self.save_button)
        actions.addWidget(self.beacon_button)
        actions.addWidget(self.status_button)
        actions.addStretch(1)
        status_card.add(actions)
        self.body.addWidget(status_card)

        for widget in (card, status_card):
            widget.setEnabled(self.can_edit)

        log_card = Card(t("Station TX Log"))
        self.log_table = DataTable(
            [
                Column("display_time", t("Time"), width=150, display=self._format_time),
                Column("kind", t("Type"), width=80, display=lambda row: t(str(row.get("kind") or ""))),
                Column("status", t("Status"), width=90, display=lambda row: t(str(row.get("status") or "")),
                       foreground=self._status_color),
                Column("interface_name", t("Interface"), width=140),
                Column("attempt_count", t("Attempts"), width=70, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                Column("last_error", t("Error"), width=200, display=lambda row: row.get("last_error") or "-",
                       tooltip=lambda row: str(row.get("last_error") or "")),
                Column("line", t("Frame"), stretch=True, mono=True, display=lambda row: row.get("line") or "-",
                       tooltip=lambda row: str(row.get("line") or ""),
                       foreground=lambda row: self._skipped_color(row)),
            ],
            sortable=False,
        )
        self.log_table.setMinimumHeight(260)
        log_card.add(self.log_table)
        self.log_empty = label(t("No station outbound jobs yet."), "muted")
        log_card.add(self.log_empty)
        self.body.addWidget(log_card)
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.log_loader = SerialLoader(self._load_log, self._apply_log, self)
        self.auto_refresh_ms = 10000
        self._loaded_form = False

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _uppercase(widget: QLineEdit) -> None:
        position = widget.cursorPosition()
        widget.setText(widget.text().upper())
        widget.setCursorPosition(position)

    @staticmethod
    def _format_time(row: dict[str, Any]) -> str:
        from app.datetime_utils import format_display_datetime

        return format_display_datetime(row.get("display_time")) if row.get("display_time") else "-"

    @staticmethod
    def _status_color(row: dict[str, Any]) -> Any:
        from app.desktop.widgets.common import tone_color

        status = str(row.get("status") or "")
        return tone_color("ok" if status == "sent" else ("error" if status == "failed" else "warn"))

    @staticmethod
    def _skipped_color(row: dict[str, Any]) -> Any:
        if row.get("is_tx_skipped"):
            from app.desktop.theme import current_colors

            return current_colors().color("muted")
        return None

    # --------------------------------------------------------------- loading
    def refresh(self) -> None:
        if not self._loaded_form:
            self.loader.request()
        self.log_loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.content import get_station_settings
        from app.services.digi_flows import has_enabled_local_tx_aprsis_flow

        with connection_scope():
            return {
                "station": get_station_settings(),
                "options": station_form_options(),
                "internal_tx_active": has_enabled_local_tx_aprsis_flow(),
            }

    def _apply(self, result: dict[str, Any]) -> None:
        self._loaded_form = True
        station = dict(result.get("station") or {})
        self.station = station
        self.internal_tx_active = bool(result.get("internal_tx_active"))
        set_combo_options(self.interface, (result.get("options") or {}).get("interface_options") or [], selected_interface_value(station))
        self.callsign.setText(str(station.get("callsign") or ""))
        set_combo_value(self.ssid, str(station.get("ssid") or ""))
        self.comment.setText(str(station.get("beacon_comment") or ""))
        mode = str(station.get("beacon_interval_mode") or "fixed")
        interval_value = "proportional" if mode == "proportional" or str(station.get("beacon_interval_minutes") or "").lower() == "proportional" else str(station.get("beacon_interval_minutes") or "30")
        self._fixed_interval = str(station.get("beacon_interval_minutes_fixed") or station.get("beacon_interval_minutes") or "30")
        set_combo_value(self.interval, interval_value)
        self.path.setText(str(station.get("beacon_path") or ""))
        self.latitude.setText(str(station.get("latitude") or ""))
        self.longitude.setText(str(station.get("longitude") or ""))
        self.symbol.set_value(station.get("symbol_table"), station.get("symbol_code"), station.get("symbol_overlay"))
        self.tx_enabled.setChecked(truthy(station.get("tx_enabled")))
        self.status_text.setText(str(station.get("status_text") or ""))
        set_combo_value(self.status_interval, str(station.get("status_interval_minutes") or "30"))
        self.status_enabled.setChecked(truthy(station.get("status_enabled")))
        self.comment_counter.update_count(self.comment.text())
        self.status_counter.update_count(self.status_text.text())
        self._sync_internal_notice()
        self._render_health()

    def _load_log(self) -> list[dict[str, Any]]:
        from app.services.content import recent_station_outbound_jobs

        return recent_station_outbound_jobs(limit=20)

    def _apply_log(self, rows: list[dict[str, Any]]) -> None:
        self.log_table.set_rows(rows or [])
        self.log_empty.setVisible(not rows)

    # ------------------------------------------------------------ behavior
    def _sync_internal_notice(self) -> None:
        from app.services.tx_scope import INTERNAL_TX_INTERFACE_OPTION_VALUE

        if combo_value(self.interface) == INTERNAL_TX_INTERFACE_OPTION_VALUE:
            if getattr(self, "internal_tx_active", False):
                self.internal_tx_notice.show_message(t("Local TX -> APRS-IS routing is active. Internal TX frames can be forwarded to APRS-IS."), True)
            else:
                self.internal_tx_notice.show_message(t("No active Local TX -> APRS-IS routing found. Internal TX currently behaves like a local black hole."), True, tone="info")
        else:
            self.internal_tx_notice.hide()

    def _interval_mode(self) -> str:
        return "proportional" if combo_value(self.interval) == "proportional" else "fixed"

    def _evaluate(self) -> dict[str, Any]:
        from app.services.beacon_pathing import evaluate_beacon_health

        mode = self._interval_mode()
        if mode == "proportional":
            minutes = int(self._fixed_interval) if str(getattr(self, "_fixed_interval", "30")).isdigit() else 30
        else:
            minutes = int(combo_value(self.interval) or 0)
        return evaluate_beacon_health(beacon_interval_mode=mode, beacon_interval_minutes=minutes, beacon_path=self.path.text())

    def _render_health(self) -> None:
        from app.services.beacon_pathing import build_proportional_schedule_lines, classify_beacon_path

        if combo_value(self.interval) != "proportional":
            self._fixed_interval = str(combo_value(self.interval) or "30")
        health = self._evaluate()
        text = t(str(health.get("headline") or ""))
        details = [t(str(line)) for line in health.get("details") or []]
        if details:
            text += "\n" + "\n".join(details)
        self.health.show_message(text, True, tone=HEALTH_TONES.get(str(health.get("tone") or "neutral"), "info"))
        proportional = self._interval_mode() == "proportional"
        self.schedule_box.setVisible(proportional)
        if proportional:
            classification = classify_beacon_path(self.path.text())
            path = classification.get("normalized_path") or ""
            self.schedule_title.setText(tf("Effective schedule for {path}:", path=path) if path else t("Effective schedule:"))
            lines = [t(line.replace("(first run)", t("(first run)"))) for line in build_proportional_schedule_lines(self.path.text())]
            self.schedule.setPlainText("\n".join(lines))

    def _pick_location(self) -> None:
        result = LocationPickerDialog.pick(self, self.latitude.text(), self.longitude.text())
        if result is not None:
            self.latitude.setText(f"{result[0]:.5f}")
            self.longitude.setText(f"{result[1]:.5f}")

    def _open_phg(self) -> None:
        dialog = PhgGeneratorDialog(self, comment=self.comment.text(), imperial=str(self.station.get("default_units") or "metric") == "imperial")
        if dialog.exec():
            self.comment.setText(insert_phg(self.comment.text(), dialog.token())[:BEACON_COMMENT_MAX])

    def collect(self) -> dict[str, Any]:
        mode = self._interval_mode()
        interval_value = str(combo_value(self.interval) or "30")
        fixed = getattr(self, "_fixed_interval", "30")
        symbol = self.symbol.values()
        return {
            "callsign": self.callsign.text().strip(),
            "ssid": str(combo_value(self.ssid) or ""),
            "beacon_interface_id": str(combo_value(self.interface) or ""),
            "beacon_comment": self.comment.text().strip(),
            "beacon_interval_minutes": interval_value,
            "beacon_interval_mode": mode,
            "beacon_interval_minutes_fixed": fixed if mode == "proportional" else interval_value,
            "beacon_path": self.path.text().strip(),
            "status_enabled": form_flag(self.status_enabled.isChecked()),
            "status_text": self.status_text.text().strip(),
            "status_interval_minutes": str(combo_value(self.status_interval) or "30"),
            "latitude": self.latitude.text().strip(),
            "longitude": self.longitude.text().strip(),
            "symbol_table": symbol["symbol_table"],
            "symbol_code": symbol["symbol_code"],
            "symbol_overlay": symbol["symbol_overlay"],
            "default_units": str(self.station.get("default_units") or "metric"),
            "tx_enabled": form_flag(self.tx_enabled.isChecked()),
        }

    def _save_payload(self) -> bool:
        from app.services.content import safe_update_station_settings

        success, error = safe_update_station_settings(self.collect())
        if not success:
            self.flash.error(t(error or "Failed to save station settings."))
            return False
        self.context.notify_data_changed("station")
        return True

    def save(self) -> None:
        health = self._evaluate()
        if health.get("requires_confirmation"):
            text = "\n".join(
                [
                    t("Beacon path is wide while the transmission interval is short."),
                    t("Recommendation:"),
                    "• " + t("use a longer interval"),
                    "• " + t("or select Proportional Path"),
                    "",
                    t("Are you sure you want to save this configuration?"),
                ]
            )
            if not confirm(self, t("This setting can significantly increase RF channel load."), text, danger=True,
                           confirm_text=t("Save despite warning")):
                return
        if self._save_payload():
            self.flash.success(t("Station settings saved."))
            self._loaded_form = False
            self.refresh()

    def _send(self, kind: str) -> None:
        question = t("Are you sure you want to send the beacon now?") if kind == "beacon" else t("Are you sure you want to send the APRS status now?")
        if not confirm(self, t("Are you sure?"), question, confirm_text=t("Send now")):
            return
        if not self._save_payload():
            return
        from app.services.content import get_station_settings
        from app.services.outbound import enqueue_beacon_job, enqueue_status_job

        station = get_station_settings()
        success, message = (enqueue_beacon_job if kind == "beacon" else enqueue_status_job)(station)
        self.flash.show_message(t(message or ""), success)
        self._loaded_form = False
        self.refresh()

    def send_beacon(self) -> None:
        self._send("beacon")

    def send_status(self) -> None:
        self._send("status")
