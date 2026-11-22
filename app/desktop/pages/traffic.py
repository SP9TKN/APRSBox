"""Traffic Monitor page (web: templates/traffic.html)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.desktop.i18n import t, tf
from app.desktop.icons import icon
from app.desktop.tasks import SerialLoader, run_async
from app.desktop.theme import current_colors
from app.desktop.widgets.common import (
    Card,
    ColorSwatch,
    FlashMessage,
    FormGrid,
    Page,
    button,
    checkbox,
    clear_layout,
    combo,
    combo_value,
    icon_button,
    label,
)
from app.desktop.widgets.traffic_log import LEGEND_ENTRIES, TrafficLogView, legend_color

TRAFFIC_TICK_MS = 1000
EXPORT_RANGES = ((15, "15 min"), (60, "1 h"), (360, "6 h"), (1440, "24 h"), (0, "All traffic kept in the database"))
EXPORT_DIRECTIONS = (("", "All directions"), ("rx", "RX"), ("tx", "TX"), ("tx-remote", "Remote client TX"))
EXPORT_FORMATS = (("txt", "Text (TXT)"), ("csv", "CSV (Excel)"))


def format_minutes(minutes: int) -> str:
    return f"{minutes // 60} h" if minutes >= 60 and minutes % 60 == 0 else f"{minutes} min"


class TrafficLogsDialog(QDialog):
    """Continuous log files per interface (on/off) and exports of the frames the database keeps."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        from app.db import get_traffic_retention_minutes
        from app.desktop.traffic_logs import enabled_interface_ids, log_root
        from app.services.content import get_configured_modem_interfaces

        self.setWindowTitle(t("Traffic logs"))
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        layout.addWidget(label(t("Traffic logs"), "h2"))
        self.flash = FlashMessage()
        layout.addWidget(self.flash)

        layout.addWidget(label(t("Continuous logging to files"), "h3"))
        layout.addWidget(label(t("Every frame of a ticked interface is appended to a daily file (UTC date). Logging works while APRSBox is running."),
                               "small", wrap=True))
        self.interfaces = get_configured_modem_interfaces()
        enabled = enabled_interface_ids()
        self.log_checks: dict[int, Any] = {}
        for item in self.interfaces:
            interface_id = int(item["id"])
            check = checkbox(f"{item['name']}  ({item['modem_type']}, {item['band'] or '-'})", interface_id in enabled)
            check.toggled.connect(lambda checked, value=interface_id: self._toggle_logging(value, checked))
            self.log_checks[interface_id] = check
            layout.addWidget(check)
        if not self.interfaces:
            layout.addWidget(label(t("No interfaces configured yet."), "muted"))
        folder_row = QHBoxLayout()
        folder_row.addWidget(label(str(log_root()), "small", wrap=True, selectable=True), 1)
        folder_row.addWidget(button(t("Open folder"), variant="secondary", icon_name="folder-open-outline", on_click=self._open_folder))
        layout.addLayout(folder_row)

        layout.addSpacing(6)
        layout.addWidget(label(t("Export"), "h3"))
        grid = FormGrid(2)
        self.export_interface = combo([("", "All interfaces")] + [(str(item["id"]), str(item["name"])) for item in self.interfaces], "")
        self.export_range = combo([(str(value), caption) for value, caption in EXPORT_RANGES], "60")
        self.export_direction = combo(EXPORT_DIRECTIONS, "")
        self.export_format = combo(EXPORT_FORMATS, "txt")
        grid.add_field("interface", t("Interface"), self.export_interface)
        grid.add_field("range", t("Time range"), self.export_range)
        grid.add_field("direction", t("Direction"), self.export_direction)
        grid.add_field("format", t("Format"), self.export_format)
        layout.addWidget(grid)
        layout.addWidget(label(tf("The database keeps the traffic of the last {retention}. Older traffic is only in the continuous log files.",
                                  retention=format_minutes(int(get_traffic_retention_minutes()))), "small", wrap=True))
        buttons = QHBoxLayout()
        buttons.addWidget(button(t("Export…"), icon_name="file-export-outline", on_click=self._export))
        buttons.addStretch(1)
        buttons.addWidget(button(t("Close"), variant="secondary", on_click=self.accept))
        layout.addLayout(buttons)

    def _toggle_logging(self, interface_id: int, enabled: bool) -> None:
        from app.desktop.traffic_logs import set_interface_logging

        set_interface_logging(interface_id, enabled)
        name = next((str(item["name"]) for item in self.interfaces if int(item["id"]) == interface_id), str(interface_id))
        self.flash.success(tf("Logging {name} to files: on." if enabled else "Logging {name} to files: off.", name=name))

    def _open_folder(self) -> None:
        from app.desktop.traffic_logs import log_root

        folder = log_root()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _export(self) -> None:
        from app.desktop.traffic_logs import export_frames, log_root

        interface_value = str(combo_value(self.export_interface) or "")
        fmt = str(combo_value(self.export_format) or "txt")
        name = next((str(item["name"]) for item in self.interfaces if str(item["id"]) == interface_value), "all")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in name)
        start = str(log_root() / "exports" / f"traffic-{safe_name}-{stamp}.{fmt}")
        Path(start).parent.mkdir(parents=True, exist_ok=True)
        file_filter = "CSV (*.csv)" if fmt == "csv" else f"{t('Text (TXT)')} (*.txt)"
        path, _filter = QFileDialog.getSaveFileName(self, t("Export traffic log"), start, file_filter)
        if not path:
            return
        try:
            count = export_frames(
                Path(path),
                interface_id=int(interface_value) if interface_value else None,
                minutes=int(combo_value(self.export_range) or 0),
                direction=str(combo_value(self.export_direction) or ""),
                fmt=fmt,
            )
        except (OSError, ValueError) as exc:
            self.flash.error(f"{t('Failed to export the traffic log.')} {exc}")
            return
        self.flash.success(tf("Exported {count} frames to {path}.", count=count, path=path))


def interface_key(item: dict[str, Any]) -> str:
    if item.get("remote_key"):  # another station's receive page (Settings -> Desktop application)
        return str(item["remote_key"])
    if isinstance(item.get("modem_id"), int):
        return f"modem:{item['modem_id']}"
    return f"fallback:{item.get('name') or ''}:{item.get('band') or ''}"


def filter_frames(snapshot: dict[str, Any], *, hidden_interfaces: set[str], quick_filter: str, text_filter: str) -> list[dict[str, Any]]:
    """Same rules as the web traffic page (interface visibility, RX/TX/remote, text)."""
    interfaces = [item for item in snapshot.get("interfaces") or [] if isinstance(item.get("modem_id"), int)]
    by_id = {item["modem_id"]: item for item in interfaces}
    visible_ids = {item["modem_id"] for item in interfaces if interface_key(item) not in hidden_interfaces}
    needle = str(text_filter or "").strip().lower()
    result: list[dict[str, Any]] = []
    for frame in snapshot.get("frames") or []:
        interface_id = frame.get("interface_id")
        if isinstance(interface_id, int) and interface_id in by_id and interface_id not in visible_ids:
            continue
        if frame.get("remote_key") and frame["remote_key"] in hidden_interfaces:
            continue
        direction = str(frame.get("direction") or "").strip().upper()
        command = str(frame.get("command") or "").strip().upper()
        if quick_filter == "rx" and direction != "RX":
            continue
        if quick_filter == "tx" and direction != "TX":
            continue
        if quick_filter == "remote" and not command.startswith("TX-PROXY"):
            continue
        if needle:
            haystack = " ".join(
                str(frame.get(key) or "")
                for key in ("source", "source_kind", "band", "direction", "command", "line")
            ).lower()
            if needle not in haystack:
                continue
        result.append(frame)
    return result


class TrafficPage(Page):
    page_key = "traffic"

    def build(self) -> None:
        self.quick_filter = ""
        self.text_filter = ""
        self.hidden_interfaces: set[str] = set()
        self.snapshot: dict[str, Any] = {"interfaces": [], "frames": []}

        card = Card()
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label(t("Traffic Log"), "h2"))
        header.addWidget(icon_button("palette-swatch-outline", t("Traffic colors"), self._show_legend, size=16))
        header.addStretch(1)
        header.addWidget(button(t("Traffic logs"), variant="secondary", icon_name="file-document-multiple-outline",
                                on_click=self._show_logs, tooltip=t("Log files per interface and export")))
        self.filter_buttons: dict[str, QPushButton] = {}
        for key, text, tooltip in (
            ("rx", "RX", t("Show only RX frames")),
            ("tx", t("TX"), t("Show only TX frames")),
            ("remote", t("Remote"), t("Show only remote client TX frames")),
        ):
            filter_button = button(text, variant="secondary", tooltip=tooltip)
            filter_button.setCheckable(True)
            filter_button.clicked.connect(lambda _checked=False, value=key: self._set_quick_filter(value))
            self.filter_buttons[key] = filter_button
            header.addWidget(filter_button)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(t("Filter"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMinimumWidth(220)
        self.filter_edit.textChanged.connect(self._text_changed)
        header.addWidget(self.filter_edit)
        self.clear_button = button(t("Clear filters"), variant="secondary", on_click=self._clear_filters)
        self.clear_button.setEnabled(False)
        header.addWidget(self.clear_button)
        self.count_label = label("", "small")
        header.addWidget(self.count_label)
        if self.can_edit:
            self.restart_button = button(t("Restart traffic monitor"), variant="secondary", icon_name="restart",
                                         on_click=self._restart_runtime)
            header.addWidget(self.restart_button)
        card.add(header)
        card.add(self.flash)
        self.interfaces_host = QWidget()
        self.interfaces_layout = QGridLayout(self.interfaces_host)
        self.interfaces_layout.setContentsMargins(0, 0, 0, 0)
        self.interfaces_layout.setHorizontalSpacing(10)
        self.interfaces_layout.setVerticalSpacing(4)
        card.add(self.interfaces_host)
        self.log = TrafficLogView()
        self.log.frame_activated.connect(self._open_frame)
        self.log.setMinimumHeight(420)
        card.add(self.log, 1)
        self.empty_label = label("", "muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.add(self.empty_label)
        self.body.addWidget(card, 1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = TRAFFIC_TICK_MS

    def activate(self, argument: Any = None) -> None:
        super().activate(argument)
        self._timer.start(TRAFFIC_TICK_MS)

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.desktop.remote_receivers import monitor_interface, monitor_rows
        from app.desktop.traffic_logs import enabled_interface_ids
        from app.services.content import traffic_snapshot

        # traffic_snapshot() may be cached: copy before adding the log switches.
        snapshot = {**traffic_snapshot(), "log_enabled": sorted(enabled_interface_ids())}
        remote = monitor_interface()
        if remote is not None:
            # The remote receiver's rows (the user: under 70 cm, 2 m and APRS-IS) within the time the log covers.
            frames = list(snapshot.get("frames") or [])
            since = min((str(frame.get("created_at") or "") for frame in frames), default="") or "0"
            merged = frames + monitor_rows(since_iso=since)
            merged.sort(key=lambda frame: str(frame.get("created_at") or ""), reverse=True)
            snapshot["frames"] = merged
            snapshot["interfaces"] = list(snapshot.get("interfaces") or []) + [remote]
        return snapshot

    def _apply(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot or {"interfaces": [], "frames": []}
        self._render()

    def _render(self) -> None:
        interfaces = list(self.snapshot.get("interfaces") or [])
        current = {interface_key(item) for item in interfaces}
        self.hidden_interfaces &= current
        self._render_interfaces(interfaces)
        frames = filter_frames(
            self.snapshot,
            hidden_interfaces=self.hidden_interfaces,
            quick_filter=self.quick_filter,
            text_filter=self.text_filter,
        )
        self.log.set_frames(frames)
        self.count_label.setText(f"{len(frames)} {t('entries')}")
        if frames:
            self.empty_label.hide()
            self.log.show()
        else:
            has_frames = bool(self.snapshot.get("frames"))
            self.empty_label.setText(t("No frames match current filters.") if has_frames else t("No frames received yet."))
            self.empty_label.show()

    def _render_interfaces(self, interfaces: list[dict[str, Any]]) -> None:
        logged = set(self.snapshot.get("log_enabled") or [])
        signature = repr([(interface_key(item), item.get("status"), item.get("status_detail"), (item.get("expose") or {}).get("active_clients"), interface_key(item) in self.hidden_interfaces, item.get("modem_id") in logged) for item in interfaces])
        if signature == getattr(self, "_interfaces_signature", None):
            return
        self._interfaces_signature = signature
        clear_layout(self.interfaces_layout)
        if not interfaces:
            self.interfaces_layout.addWidget(label(t("No enabled interface is configured."), "muted"), 0, 0, 1, 5)
            return
        for row, item in enumerate(interfaces):
            key = interface_key(item)
            visible = key not in self.hidden_interfaces
            toggle = QToolButton()
            toggle.setProperty("variant", "icon")
            toggle.setIcon(icon("eye" if visible else "eye-off", None, 16))
            toggle.setToolTip(t("Hide") if visible else t("Show"))
            toggle.clicked.connect(lambda _checked=False, value=key: self._toggle_interface(value))
            if isinstance(item.get("modem_id"), int):
                is_logged = item["modem_id"] in logged
                log_toggle = QToolButton()
                log_toggle.setProperty("variant", "icon")
                log_toggle.setIcon(icon("record-rec" if is_logged else "record-circle-outline",
                                        current_colors().color("error") if is_logged else None, 16))
                log_toggle.setToolTip(t("Log to file: on (click to turn off)") if is_logged else t("Log to file: off (click to turn on)"))
                log_toggle.clicked.connect(lambda _checked=False, value=item["modem_id"], name=str(item.get("name") or ""), on=is_logged:
                                           self._toggle_log(value, name, not on))
                self.interfaces_layout.addWidget(log_toggle, row, 4)
            parts = [str(item.get("status") or "-"), str(item.get("status_detail") or "")]
            if item.get("remote_key"):
                parts = [str(item.get("status") or "-"), tf("receive page {url}, read every minute", url=item.get("url") or "")]
                if item.get("last_new_row"):
                    parts.append(tf("last new entry {time}", time=item["last_new_row"]))
            expose = item.get("expose") or {}
            if expose.get("listen_endpoint"):
                parts.append(f"{t('Listening')} {expose.get('listen_endpoint')}")
                parts.append(f"{t('Clients')}: {expose.get('active_clients') or 0}")
            status_label = label(" · ".join(part for part in parts if part), "mono" if False else None, wrap=True, selectable=True)
            band = label(str(item.get("band") or "-"), "muted")
            name = label(str(item.get("name") or "-"), "h3")
            for widget in (band, name, status_label):
                widget.setEnabled(visible)
            self.interfaces_layout.addWidget(toggle, row, 0)
            self.interfaces_layout.addWidget(band, row, 1)
            self.interfaces_layout.addWidget(name, row, 2)
            self.interfaces_layout.addWidget(status_label, row, 3)
        self.interfaces_layout.setColumnStretch(3, 1)

    def _toggle_log(self, interface_id: int, name: str, enabled: bool) -> None:
        from app.desktop.traffic_logs import set_interface_logging

        set_interface_logging(interface_id, enabled)
        logged = set(self.snapshot.get("log_enabled") or [])
        logged = logged | {interface_id} if enabled else logged - {interface_id}
        self.snapshot = {**self.snapshot, "log_enabled": sorted(logged)}
        self.flash.success(tf("Logging {name} to files: on." if enabled else "Logging {name} to files: off.", name=name))
        self._render_interfaces(list(self.snapshot.get("interfaces") or []))

    def _show_logs(self) -> None:
        dialog = TrafficLogsDialog(self)
        dialog.exec()
        dialog.deleteLater()
        self.refresh()

    def _toggle_interface(self, key: str) -> None:
        if key in self.hidden_interfaces:
            self.hidden_interfaces.discard(key)
        else:
            self.hidden_interfaces.add(key)
        self._interfaces_signature = None
        self._render()

    def _set_quick_filter(self, value: str) -> None:
        self.quick_filter = "" if self.quick_filter == value else value
        for key, filter_button in self.filter_buttons.items():
            filter_button.setChecked(key == self.quick_filter)
        self._sync_clear()
        self._render()

    def _text_changed(self, text: str) -> None:
        self.text_filter = text.strip().lower()
        self._sync_clear()
        self._render()

    def _clear_filters(self) -> None:
        self.quick_filter = ""
        self.filter_edit.clear()
        for filter_button in self.filter_buttons.values():
            filter_button.setChecked(False)
        self._sync_clear()
        self._render()

    def _sync_clear(self) -> None:
        self.clear_button.setEnabled(bool(self.quick_filter or self.text_filter))

    def _show_legend(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(t("Traffic colors"))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.addWidget(label(t("Traffic colors"), "h2"))
        for row_class, caption in LEGEND_ENTRIES:
            row = QHBoxLayout()
            row.addWidget(ColorSwatch(legend_color(row_class), 14))
            row.addWidget(label(t(caption)), 1)
            layout.addLayout(row)
        close = button(t("Close"), variant="secondary", on_click=dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _open_frame(self, frame: dict[str, Any]) -> None:
        if frame.get("alert_href") and frame.get("alert_id"):
            self.navigate("alert-detail", int(frame["alert_id"]))
            return
        if frame.get("id"):
            self.navigate("frame-detail", int(frame["id"]))

    def _restart_runtime(self) -> None:
        runtime = getattr(self.context, "runtime", None)
        self.restart_button.setEnabled(False)

        def work() -> dict[str, Any]:
            from app.services.core_client import restart_core_traffic_monitor

            return restart_core_traffic_monitor()

        def done(result: dict[str, Any]) -> None:
            self.restart_button.setEnabled(True)
            if result.get("ok"):
                self.flash.success(t("Traffic monitor runtime restarted."))
            else:
                self.flash.error(t(str(result.get("error") or "Traffic monitor restart failed.")))
            self.refresh()

        if runtime is None or not getattr(runtime, "running", False):
            done({"ok": False, "error": "APRS runtime is not running."})
            return
        run_async(work, done, owner=self)
