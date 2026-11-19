"""Interfaces page (web: section.html for "modems" + partials/modem_form_fields.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon, icon_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.common import (
    Card,
    FormGrid,
    Page,
    ToneLabel,
    button,
    checkbox,
    clear_layout,
    combo,
    combo_value,
    confirm,
    form_flag,
    icon_button,
    key_value_grid,
    label,
    line_edit,
    set_combo_value,
    tone_color,
    truthy,
)
from app.desktop.widgets.table import Column, DataTable

MODEM_TYPES = (
    ("SERIALL", "SERIALL"),
    ("TCP", "TCP"),
    ("OPENWEBRX_MQTT", "OpenWebRX MQTT (RX only)"),
    ("APRSIS", "APRS-IS (RX/TX)"),
)
BAND_OPTIONS = (("", "No band condition assessment"), ("2m", "2m — assess propagation"), ("70cm", "70cm — assess propagation"))
SILENCE_OPTIONS = tuple((str(seconds), str(seconds)) for seconds in range(0, 601, 30))
BAUD_RATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

RUNTIME_STATUS_TONES = {
    "connected": "ok",
    "enabled": "ok",
    "running": "ok",
    "connecting": "warn",
    "warning": "warn",
    "error": "error",
    "disconnected": "error",
    "disabled": "neutral",
}


def save_interface(payload: dict[str, Any], aprsis: dict[str, Any], record_id: int | None) -> tuple[bool, str | None]:
    """Port of pages.modems_create (validation, single APRSIS rule, MQTT mask, APRS-IS config)."""
    from app.db import fetch_one
    from app.services.aprsis import normalize_aprsis_config_payload, save_aprsis_config
    from app.services.content import safe_create_section_row, safe_update_section_row
    from app.services.mqtt_url import OPENWEBRX_MQTT_MODEM_TYPE, mask_mqtt_url

    modem_type = str(payload.get("modem_type") or "").strip().upper()
    if modem_type == "SERIAL":
        modem_type = "SERIALL"
    if modem_type not in {"SERIALL", "TCP", OPENWEBRX_MQTT_MODEM_TYPE, "APRSIS"}:
        return False, "Unsupported interface type."
    payload = dict(payload)
    payload["modem_type"] = modem_type
    device_path = str(payload.get("device_path") or "").strip()
    if record_id is not None and modem_type == OPENWEBRX_MQTT_MODEM_TYPE and "***" in device_path:
        existing = fetch_one("SELECT modem_type, device_path FROM modems WHERE id = ?", (record_id,))
        if existing is not None:
            existing_type = str(existing["modem_type"] or "").strip().upper()
            existing_path = str(existing["device_path"] or "").strip()
            if existing_type == OPENWEBRX_MQTT_MODEM_TYPE and mask_mqtt_url(existing_path) == device_path:
                device_path = existing_path
    payload["device_path"] = device_path
    normalized_aprsis = None
    if modem_type == "APRSIS":
        try:
            normalized_aprsis = normalize_aprsis_config_payload(
                {
                    "server": aprsis.get("aprsis_server"),
                    "port": aprsis.get("aprsis_port"),
                    "login": aprsis.get("aprsis_login"),
                    "passcode": aprsis.get("aprsis_passcode"),
                }
            )
        except ValueError as exc:
            return False, str(exc)
    if record_id is None:
        if modem_type == "APRSIS" and fetch_one("SELECT id FROM modems WHERE UPPER(modem_type) = 'APRSIS' LIMIT 1") is not None:
            return False, "An APRSIS interface already exists. Edit the existing interface instead."
        success, error = safe_create_section_row("modems", payload)
    else:
        success, error = safe_update_section_row("modems", record_id, payload)
    if success and normalized_aprsis is not None:
        save_aprsis_config(normalized_aprsis)
    return success, error


class ExposeBlock(QWidget):
    """Expose Port block shared by SERIALL and TCP panels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = checkbox(t("Expose Port"))
        self.enabled.toggled.connect(self._sync)
        layout.addWidget(self.enabled)
        self.details = FormGrid(2)
        self.allow_tx = checkbox(t("Allow TX from remote clients"), True)
        self.details.add_widget(self.allow_tx)
        self.bind = line_edit("0.0.0.0")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(8002)
        self.whitelist = QPlainTextEdit()
        self.whitelist.setPlaceholderText(t("192.168.1.10\n192.168.1.0/24"))
        self.whitelist.setFixedHeight(70)
        self.details.add_field("expose_bind_address", t("Bind Address"), self.bind)
        self.details.add_field("expose_port", t("Port"), self.port)
        self.details.add_field("expose_whitelist", t("Whitelist"), self.whitelist, span=2)
        layout.addWidget(self.details)
        self._sync()

    def _sync(self) -> None:
        self.details.setVisible(self.enabled.isChecked())

    def load(self, values: dict[str, Any]) -> None:
        self.enabled.setChecked(truthy(values.get("expose_port_enabled")))
        self.allow_tx.setChecked(truthy(values.get("expose_allow_tx", True)))
        self.bind.setText(str(values.get("expose_bind_address") or "0.0.0.0"))
        try:
            self.port.setValue(int(values.get("expose_port") or 8002))
        except (TypeError, ValueError):
            self.port.setValue(8002)
        self.whitelist.setPlainText(str(values.get("expose_whitelist") or ""))
        self._sync()

    def values(self) -> dict[str, Any]:
        enabled = self.enabled.isChecked()
        return {
            "expose_port_enabled": form_flag(enabled),
            "expose_allow_tx": form_flag(self.allow_tx.isChecked()) if enabled else None,
            "expose_bind_address": self.bind.text().strip() if enabled else "0.0.0.0",
            "expose_port": self.port.value() if enabled else 8002,
            "expose_whitelist": self.whitelist.toPlainText() if enabled else "",
        }


class TncPanel(QWidget):
    """SERIALL / TCP settings."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        grid = FormGrid(2)
        if kind == "SERIALL":
            self.path = QComboBox()
            self.path.setEditable(True)
            self.path.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.refresh_ports()
            path_row = QWidget()
            path_layout = QHBoxLayout(path_row)
            path_layout.setContentsMargins(0, 0, 0, 0)
            path_layout.addWidget(self.path, 1)
            path_layout.addWidget(icon_button("refresh", t("Refresh serial port list"), self.refresh_ports, size=16))
            grid.add_field("device_path", t("Path / Address / Filter"), path_row,
                           help_text=t("COM port (e.g. COM5) or a pyserial URL such as socket://host:port."))
        else:
            self.path = line_edit("", placeholder="127.0.0.1:8001")
            grid.add_field("device_path", t("Path / Address / Filter"), self.path)
        self.band = combo(BAND_OPTIONS, "")
        grid.add_field("band", t("Band condition assessment"), self.band)
        if kind == "SERIALL":
            self.baud = QComboBox()
            self.baud.setEditable(True)
            for rate in BAUD_RATES:
                self.baud.addItem(str(rate), rate)
            self.baud.setCurrentText("9600")
            grid.add_field("baud_rate", t("Baud Rate"), self.baud)
        self.silence = combo(SILENCE_OPTIONS, "150")
        grid.add_field("serial_rx_silence_reconnect_seconds", t("RX Silence Reconnect Timeout (s)"), self.silence)
        layout.addWidget(grid)
        capability = QFrame()
        capability.setProperty("card", "soft")
        capability_layout = QHBoxLayout(capability)
        capability_layout.setContentsMargins(10, 8, 10, 8)
        self.tx_blocked = checkbox(t("Block TX on this interface"))
        capability_layout.addWidget(self.tx_blocked, 1)
        capability_layout.addWidget(label(t("TX Min Gap (s)")))
        self.tx_gap = QDoubleSpinBox()
        self.tx_gap.setRange(0.2, 1.2)
        self.tx_gap.setSingleStep(0.01)
        self.tx_gap.setDecimals(2)
        self.tx_gap.setValue(0.35)
        capability_layout.addWidget(self.tx_gap)
        layout.addWidget(capability)
        expose_frame = QFrame()
        expose_frame.setProperty("card", "soft")
        expose_layout = QVBoxLayout(expose_frame)
        expose_layout.setContentsMargins(10, 8, 10, 8)
        self.expose = ExposeBlock()
        expose_layout.addWidget(self.expose)
        layout.addWidget(expose_frame)

    def refresh_ports(self) -> None:
        if self.kind != "SERIALL":
            return
        from app.services.serial_tnc import list_serial_ports

        current = self.path.currentText()
        self.path.blockSignals(True)
        self.path.clear()
        for port in list_serial_ports():
            text = port["device"]
            self.path.addItem(f"{text}  —  {port['description']}" if port.get("description") else text, text)
        self.path.setCurrentText(current)
        self.path.blockSignals(False)

    def device_path(self) -> str:
        if isinstance(self.path, QComboBox):
            text = self.path.currentText().strip()
            index = self.path.findText(text)
            if index >= 0 and self.path.itemData(index):
                return str(self.path.itemData(index))
            return text.split("  —  ", 1)[0].strip()
        return self.path.text().strip()

    def load(self, values: dict[str, Any]) -> None:
        path = str(values.get("device_path") or "") if str(values.get("modem_type") or "").upper() == self.kind else ""
        if isinstance(self.path, QComboBox):
            self.path.setCurrentText(path)
        else:
            self.path.setText(path)
        set_combo_value(self.band, values.get("band") or "")
        if self.kind == "SERIALL":
            self.baud.setCurrentText(str(values.get("baud_rate") or "9600"))
        set_combo_value(self.silence, str(values.get("serial_rx_silence_reconnect_seconds", 150)))
        self.tx_blocked.setChecked(truthy(values.get("tx_blocked")))
        try:
            self.tx_gap.setValue(float(values.get("tx_min_gap_seconds", 0.35) or 0.35))
        except (TypeError, ValueError):
            self.tx_gap.setValue(0.35)
        self.expose.load(values)

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "device_path": self.device_path(),
            "band": combo_value(self.band) or "",
            "serial_rx_silence_reconnect_seconds": int(combo_value(self.silence) or 150),
            "tx_blocked": form_flag(self.tx_blocked.isChecked()),
            "tx_min_gap_seconds": f"{self.tx_gap.value():.2f}",
        }
        if self.kind == "SERIALL":
            text = self.baud.currentText().strip()
            result["baud_rate"] = int(text) if text.isdigit() else None
        else:
            result["baud_rate"] = None
        result.update(self.expose.values())
        return result


class MqttPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = FormGrid(2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.path = line_edit("", placeholder="mqtt://user:pass@127.0.0.1:1883/openwebrx/aprs")
        grid.add_field("device_path", t("Path / Address / Filter"), self.path, span=2)
        self.band = combo(BAND_OPTIONS, "")
        grid.add_field("band", t("Band condition assessment"), self.band)
        layout.addWidget(grid)

    def load(self, values: dict[str, Any]) -> None:
        path = str(values.get("device_path") or "") if str(values.get("modem_type") or "").upper() == "OPENWEBRX_MQTT" else ""
        self.path.setText(path)
        set_combo_value(self.band, values.get("band") or "")

    def values(self) -> dict[str, Any]:
        return {"device_path": self.path.text().strip(), "band": combo_value(self.band) or "", "baud_rate": None}


class AprsisPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = FormGrid(2)
        self.server = line_edit("")
        self.port = line_edit("")
        self.login = line_edit("")
        self.passcode = line_edit("")
        self.filter = line_edit("m/20", placeholder="m/20")
        grid.add_field("aprsis_server", t("Server"), self.server)
        grid.add_field("aprsis_port", t("Port"), self.port)
        self.login_hint = label("", "small")
        self.passcode_hint = label("", "small")
        grid.add_field("aprsis_login", t("Login callsign / callsign-SSID"), self.login)
        grid.add_field("aprsis_passcode", t("Passcode"), self.passcode)
        grid.add_widget(self.login_hint, span=1)
        grid.add_widget(self.passcode_hint, span=1)
        grid.add_field("device_path", t("APRS-IS receive filter"), self.filter, span=2)
        layout.addWidget(grid)

    def load(self, values: dict[str, Any], config: dict[str, Any]) -> None:
        self.server.setText(str(values.get("aprsis_server") or config.get("server") or ""))
        self.port.setText(str(values.get("aprsis_port") or config.get("port") or ""))
        default_login = "" if config.get("login_is_default") else str(config.get("login") or "")
        default_passcode = "" if config.get("passcode_is_default") else str(config.get("passcode") or "")
        self.login.setText(str(values.get("aprsis_login", default_login) or ""))
        self.passcode.setText(str(values.get("aprsis_passcode", default_passcode) or ""))
        self.login_hint.setText(tf("Default from My Station: {value}", value=config.get("station_login_default") or "-"))
        self.passcode_hint.setText(tf("Default auto-derived passcode: {value}", value=config.get("passcode_default") or "-"))
        is_aprsis = str(values.get("modem_type") or "").upper() == "APRSIS"
        self.filter.setText(str(values.get("device_path") or "") if is_aprsis else "m/20")

    def values(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {"device_path": self.filter.text().strip(), "band": "", "baud_rate": None},
            {
                "aprsis_server": self.server.text().strip(),
                "aprsis_port": self.port.text().strip(),
                "aprsis_login": self.login.text().strip(),
                "aprsis_passcode": self.passcode.text().strip(),
            },
        )


class InterfacesPage(Page):
    page_key = "modems"

    def build(self) -> None:
        self.edit_id: int | None = None
        self.rows: list[dict[str, Any]] = []
        self.aprsis_config: dict[str, Any] = {}
        layout = QHBoxLayout()
        layout.setSpacing(14)

        list_card = Card(t("Interface List"))
        if self.can_edit:
            list_card.add_action(button(t("Add Interface"), icon_name="plus", on_click=self.new_interface))
        columns = [
            Column("name", t("Name"), stretch=True, bold=lambda row: True,
                   display=lambda row: row.get("name") or "",
                   secondary=lambda row: f"{row.get('modem_type') or ''} · {row.get('device_path') or ''}"),
            Column("enabled", t("Status"), width=86, display=lambda row: "",
                   icon=lambda row: icon_pixmap(str(row.get("modem_runtime_icon") or ("check-circle-outline.svg" if row.get("enabled") else "close-circle-outline.svg")).removesuffix(".svg"),
                                                tone_color(RUNTIME_STATUS_TONES.get(str(row.get("modem_runtime_status") or ("enabled" if row.get("enabled") else "disabled")), "neutral")), 20),
                   tooltip=self._status_tooltip),
            Column("tx_blocked", t("TX control"), width=120, display=lambda row: "", icon=self._tx_icon, tooltip=self._tx_tooltip),
            Column("expose_port_enabled", t("LAN"), width=60, display=lambda row: "",
                   icon=lambda row: icon_pixmap("lan-check" if row.get("expose_port_enabled") else "lan-disconnect",
                                                tone_color("ok" if row.get("expose_port_enabled") else "neutral"), 20),
                   tooltip=lambda row: t("On") if row.get("expose_port_enabled") else t("Off")),
        ]
        self.table = DataTable(columns, row_height=46, sortable=False)
        self.table.row_activated.connect(lambda row: self.edit_interface(int(row["id"])))
        self.table.clicked.connect(lambda index: self._row_clicked())
        list_card.add(self.table, 1)
        self.table.setMinimumHeight(260)
        if self.can_edit:
            actions = QHBoxLayout()
            self.toggle_button = button(t("Disable interface"), variant="secondary", icon_name="pause-circle-outline",
                                        on_click=self._toggle_selected)
            self.delete_button = button(t("Delete record"), variant="danger", icon_name="delete-outline", on_click=self._delete_selected)
            actions.addWidget(self.toggle_button)
            actions.addWidget(self.delete_button)
            actions.addStretch(1)
            list_card.add(actions)
            self._sync_actions()
        self.empty_label = label(t("No records yet."), "muted")
        list_card.add(self.empty_label)
        layout.addWidget(list_card, 5)

        self.form_card = Card(t("Add Interface"))
        self.form_card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/tnc")))
        self.form_card.add(self.flash)
        base = FormGrid(2)
        self.name = line_edit("")
        base.add_field("name", t("Name"), self.name)
        self.type_combo = combo(MODEM_TYPES, "SERIALL")
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        base.add_field("modem_type", t("Type"), self.type_combo)
        self.enabled_check = checkbox(t("Enabled"))
        base.add_widget(self.enabled_check)
        self.form_card.add(base)
        self.type_box = QGroupBox()
        type_layout = QVBoxLayout(self.type_box)
        self.stack = QStackedWidget()
        self.serial_panel = TncPanel("SERIALL")
        self.tcp_panel = TncPanel("TCP")
        self.mqtt_panel = MqttPanel()
        self.aprsis_panel = AprsisPanel()
        for panel in (self.serial_panel, self.tcp_panel, self.mqtt_panel, self.aprsis_panel):
            self.stack.addWidget(panel)
        type_layout.addWidget(self.stack)
        self.form_card.add(self.type_box)
        self.form_buttons = QHBoxLayout()
        self.save_button = button(t("Save"), icon_name="content-save-outline", on_click=self.save)
        self.cancel_button = button(t("Cancel"), variant="secondary", on_click=self.new_interface)
        self.form_buttons.addWidget(self.save_button)
        self.form_buttons.addWidget(self.cancel_button)
        self.form_buttons.addStretch(1)
        self.form_card.add(self.form_buttons)
        self.aprsis_status = Card(t("APRS-IS connection status"), variant="soft")
        self.aprsis_status_body = QVBoxLayout()
        self.aprsis_status.add(self.aprsis_status_body)
        self.form_card.add(self.aprsis_status)
        self.form_card.body.addStretch(1)
        for widget in (self.name, self.type_combo, self.enabled_check, self.stack, self.save_button):
            widget.setEnabled(self.can_edit)
        layout.addWidget(self.form_card, 6)
        self.body.addLayout(layout, 1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.status_loader = SerialLoader(self._load_status, self._apply_status, self)
        self.auto_refresh_ms = 5000
        self.new_interface()

    # ------------------------------------------------------------------ data
    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict):
            if argument.get("edit"):
                try:
                    self._pending_edit = int(argument["edit"])
                except (TypeError, ValueError):
                    self._pending_edit = None
            elif str(argument.get("new_type") or "").upper() in {key for key, _label in MODEM_TYPES}:
                self.new_interface(str(argument.get("new_type")).upper())

    def refresh(self) -> None:
        self.loader.request()
        self.status_loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.aprsis import get_aprsis_config
        from app.services.content import get_section_rows

        with connection_scope():
            return {"rows": get_section_rows("modems"), "aprsis_config": get_aprsis_config()}

    def _apply(self, result: dict[str, Any]) -> None:
        self.rows = list(result.get("rows") or [])
        self.aprsis_config = result.get("aprsis_config") or {}
        self.table.set_rows(self.rows)
        self.empty_label.setVisible(not self.rows)
        pending = getattr(self, "_pending_edit", None)
        if pending is not None:
            self._pending_edit = None
            self.edit_interface(pending)
        elif self.edit_id is None and not self.name.text():
            self.aprsis_panel.load({"modem_type": combo_value(self.type_combo)}, self.aprsis_config)
        self._sync_actions()

    def _load_status(self) -> dict[str, Any]:
        from app.services.aprsis import aprsis_runtime_badge, get_aprsis_config, get_aprsis_diagnostics, get_aprsis_runtime_status

        runtime = get_aprsis_runtime_status()
        return {
            "runtime": runtime,
            "badge": aprsis_runtime_badge(runtime.get("status", "")),
            "config": get_aprsis_config(),
            "diagnostics": get_aprsis_diagnostics(),
        }

    def _apply_status(self, status: dict[str, Any]) -> None:
        from app.datetime_utils import format_display_datetime

        clear_layout(self.aprsis_status_body)
        runtime = status.get("runtime") or {}
        config = status.get("config") or {}
        diag = status.get("diagnostics") or {}
        badge = str(status.get("badge") or "disabled")
        tone = {"enabled": "ok", "warning": "warn"}.get(badge, "error")
        state_text = str(runtime.get("status") or "-")
        header = QHBoxLayout()
        header.addWidget(label(str(runtime.get("status_detail") or "-"), "small", wrap=True), 1)
        header.addWidget(ToneLabel(t(state_text.capitalize()), tone))
        self.aprsis_status_body.addLayout(header)
        server = runtime.get("server") or config.get("server") or "-"
        port = runtime.get("port") or config.get("port")
        rows = [
            (t("Server"), f"{server}:{port}" if port else server),
            (t("Login"), runtime.get("login") or config.get("login") or "-"),
            (t("Connected at"), format_display_datetime(runtime.get("connected_at")) if runtime.get("connected_at") else "-"),
            (t("Active APRSIS routing flows"), diag.get("active_flow_count", 0)),
            (t("Last error"), runtime.get("last_error") or "-"),
        ]
        self.aprsis_status_body.addWidget(key_value_grid(rows))
        tx = diag.get("tx") or {}
        strict = diag.get("strict_rejects") or {}
        details = [
            (t("Enabled APRSIS flow names"), ", ".join(diag.get("active_flow_names") or []) or "-"),
            (t("Last routing activity"), format_display_datetime(diag.get("last_activity_at")) if diag.get("last_activity_at") else "-"),
            (t("Frames sent to APRS-IS (total / 1h / 24h)"), f"{tx.get('sent_total', 0)} / {tx.get('sent_1h', 0)} / {tx.get('sent_24h', 0)}"),
            (t("Frames dropped before APRS-IS TX (total / 1h / 24h)"), f"{tx.get('drop_total', 0)} / {tx.get('drop_1h', 0)} / {tx.get('drop_24h', 0)}"),
            (t("Strict guard rejects (total / 1h / 24h)"), f"{strict.get('total', 0)} / {strict.get('last_1h', 0)} / {strict.get('last_24h', 0)}"),
        ]
        self.aprsis_status_body.addWidget(label(t("iGATE diagnostics"), "h3"))
        self.aprsis_status_body.addWidget(key_value_grid(details))

    # ----------------------------------------------------------------- table
    def _status_tooltip(self, row: dict[str, Any]) -> str:
        parts = [t(str(row.get("modem_runtime_label") or ("Enabled" if row.get("enabled") else "Disabled")))]
        if row.get("modem_runtime_title"):
            parts.append(t(str(row.get("modem_runtime_title"))))
        if row.get("aprsis_direction_title"):
            parts.append(t(str(row.get("aprsis_direction_title"))))
        return " · ".join(parts)

    def _tx_icon(self, row: dict[str, Any]) -> Any:
        if str(row.get("modem_type") or "").upper() == "APRSIS":
            enabled = bool(row.get("aprsis_tx_enabled"))
            return icon_pixmap("source-branch-check" if enabled else "source-branch-minus", tone_color("ok" if enabled else "neutral"), 20)
        blocked = bool(row.get("tx_blocked"))
        return icon_pixmap("lock" if blocked else "lock-open", tone_color("error" if blocked else "ok"), 20)

    def _tx_tooltip(self, row: dict[str, Any]) -> str:
        if str(row.get("modem_type") or "").upper() == "APRSIS":
            if row.get("aprsis_tx_enabled"):
                return t("APRS-IS TX is enabled by Packet Routing.")
            if row.get("aprsis_tx_configured"):
                return t("APRS-IS TX flow is configured, but the APRS-IS connection is disabled.")
            return t("APRS-IS TX is disabled because no active TX APRS-IS flow exists.")
        return t("On") if row.get("tx_blocked") else t("Off")

    def _row_clicked(self) -> None:
        row = self.table.current_row()
        self._sync_actions()
        if row is not None and self.can_edit:
            self.edit_interface(int(row["id"]))

    def _sync_actions(self) -> None:
        if not self.can_edit:
            return
        row = self.table.current_row()
        self.toggle_button.setEnabled(row is not None)
        self.delete_button.setEnabled(row is not None)
        if row is not None and row.get("enabled"):
            self.toggle_button.setText(t("Disable interface"))
            self.toggle_button.setIcon(icon("pause-circle-outline", None, 16))
        else:
            self.toggle_button.setText(t("Enable interface"))
            self.toggle_button.setIcon(icon("play-circle-outline", None, 16))

    def _toggle_selected(self) -> None:
        row = self.table.current_row()
        if row is None:
            return
        from app.services.content import set_modem_enabled

        try:
            set_modem_enabled(int(row["id"]), not bool(row.get("enabled")))
        except ValueError as exc:
            self.flash.error(t(str(exc)))
            return
        self.flash.success(t("Interface status updated."))
        self.context.notify_data_changed("modems")
        self.refresh()

    def _delete_selected(self) -> None:
        row = self.table.current_row()
        if row is None:
            return
        if not confirm(self, t("Delete record"), t("Delete this record?") + f"\n\n{row.get('name')}", danger=True, confirm_text=t("Delete")):
            return
        from app.services.content import delete_section_row

        delete_section_row("modems", int(row["id"]))
        if self.edit_id == int(row["id"]):
            self.new_interface()
        self.flash.success(t("Record deleted."))
        self.context.notify_data_changed("modems")
        self.refresh()

    # ------------------------------------------------------------------ form
    def _type_changed(self) -> None:
        modem_type = str(combo_value(self.type_combo) or "SERIALL")
        index = {"SERIALL": 0, "TCP": 1, "OPENWEBRX_MQTT": 2, "APRSIS": 3}.get(modem_type, 0)
        self.stack.setCurrentIndex(index)
        self.type_box.setTitle(t(dict(MODEM_TYPES).get(modem_type, modem_type)))
        self.enabled_check.setText(t("Enable APRS-IS connection") if modem_type == "APRSIS" else t("Enabled"))
        self.aprsis_status.setVisible(modem_type == "APRSIS")
        # Only the shown panel counts for the stack's height, so the stack follows it when it grows
        # (ticking Expose Port shows its details). A fixed height left them clipped out of view.
        current = self.stack.currentWidget()
        for index in range(self.stack.count()):
            panel = self.stack.widget(index)
            vertical = QSizePolicy.Policy.Preferred if panel is current else QSizePolicy.Policy.Ignored
            panel.setSizePolicy(QSizePolicy.Policy.Preferred, vertical)

    def new_interface(self, modem_type: str = "SERIALL") -> None:
        self.edit_id = None
        self.form_card.set_title(t("Add Interface"))
        values = {"modem_type": modem_type, "serial_rx_silence_reconnect_seconds": 150, "tx_min_gap_seconds": 0.35,
                  "expose_allow_tx": True, "expose_bind_address": "0.0.0.0", "expose_port": 8002}
        self._load_form(values)
        self.cancel_button.setVisible(False)

    def edit_interface(self, record_id: int) -> None:
        from app.services.content import get_section_row

        row = get_section_row("modems", record_id)
        if row is None:
            self.flash.error(t("Interface not found."))
            return
        self.edit_id = record_id
        self.form_card.set_title(t("Edit Interface"))
        self._load_form(dict(row))
        self.cancel_button.setVisible(True)
        self.table.select_key(record_id, scroll=False)

    def _load_form(self, values: dict[str, Any]) -> None:
        self.name.setText(str(values.get("name") or ""))
        set_combo_value(self.type_combo, str(values.get("modem_type") or "SERIALL").upper())
        self.enabled_check.setChecked(truthy(values.get("enabled")))
        for panel in (self.serial_panel, self.tcp_panel, self.mqtt_panel):
            panel.load(values)
        self.aprsis_panel.load(values, self.aprsis_config)
        self._type_changed()

    def collect(self) -> tuple[dict[str, Any], dict[str, Any]]:
        modem_type = str(combo_value(self.type_combo) or "SERIALL")
        payload: dict[str, Any] = {
            "name": self.name.text().strip(),
            "modem_type": modem_type,
            "enabled": form_flag(self.enabled_check.isChecked()),
            "tx_blocked": None,
            "tx_min_gap_seconds": "0.35",
            "serial_rx_silence_reconnect_seconds": 150,
            "expose_port_enabled": None,
            "expose_allow_tx": None,
            "expose_bind_address": "0.0.0.0",
            "expose_port": 8002,
            "expose_whitelist": "",
        }
        aprsis: dict[str, Any] = {}
        if modem_type == "SERIALL":
            payload.update(self.serial_panel.values())
        elif modem_type == "TCP":
            payload.update(self.tcp_panel.values())
        elif modem_type == "OPENWEBRX_MQTT":
            payload.update(self.mqtt_panel.values())
        else:
            values, aprsis = self.aprsis_panel.values()
            payload.update(values)
        return payload, aprsis

    def save(self) -> None:
        payload, aprsis = self.collect()
        success, error = save_interface(payload, aprsis, self.edit_id)
        if not success:
            self.flash.error(t(error or "Failed to save interface settings."))
            return
        if self.edit_id is None:
            from app.db import fetch_one

            row = fetch_one("SELECT id FROM modems WHERE name = ? ORDER BY id DESC LIMIT 1", (payload["name"],))
            if row is not None:
                self.edit_id = int(row["id"])
                self.form_card.set_title(t("Edit Interface"))
                self.cancel_button.setVisible(True)
        self.flash.success(t("Interface settings updated."))
        self.context.notify_data_changed("modems")
        self.refresh()
