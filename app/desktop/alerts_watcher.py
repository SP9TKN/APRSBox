"""Emergency / APRS alert pop-up (web: partials/emergency_modal.html + map-emergency-modal.js)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.desktop import context as ctx
from app.desktop import sound
from app.desktop.i18n import t
from app.desktop.icons import icon_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors
from app.desktop.widgets.common import Badge, label

ALERT_POLL_MS = 1500


def popup_frame_data(frame: dict[str, Any]) -> dict[str, Any]:
    return dict(frame.get("alert_popup_data") or frame.get("emergency_data") or {})


def emergency_signature(frame: dict[str, Any]) -> str:
    if frame.get("alert_popup_kind") == "alarm_group" and frame.get("alert_id"):
        return f"alarm-group|{frame.get('alert_id')}"
    data = popup_frame_data(frame)
    return "|".join(
        str(part or "")
        for part in (
            frame.get("alert_id") or "",
            data.get("timestamp_utc") or frame.get("timestamp") or "",
            data.get("raw_frame") or frame.get("line") or "",
        )
    )


def frame_sort_value(frame: dict[str, Any]) -> float:
    data = popup_frame_data(frame)
    text = str(data.get("timestamp_utc") or frame.get("timestamp") or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def select_newest_popup_frame(frames: list[dict[str, Any]], handled: Any) -> dict[str, Any] | None:
    candidates = [
        frame
        for frame in frames
        if isinstance(frame, dict)
        and (frame.get("alert_popup") or frame.get("emergency"))
        and frame.get("alert_should_notify")
        and not frame.get("alert_muted")
        and emergency_signature(frame) not in handled
    ]
    candidates.sort(key=frame_sort_value, reverse=True)
    return candidates[0] if candidates else None


def format_utc(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class EmergencyDialog(QDialog):
    def __init__(self, watcher: "AlertWatcher", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.watcher = watcher
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowTitle(t("Emergency frame received"))
        self.resize(720, 680)
        self.frame: dict[str, Any] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.icon_label = QLabel()
        header.addWidget(self.icon_label)
        titles = QVBoxLayout()
        self.title = label(t("Emergency frame received"), "page-title")
        self.callsign = label("-", "h2")
        titles.addWidget(self.title)
        titles.addWidget(self.callsign)
        header.addLayout(titles, 1)
        self.status = Badge(t("Active emergency"), "danger")
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        self.newer = label(t("Newer emergency received"), None)
        self.newer.setProperty("flash", True)
        self.newer.setProperty("tone", "error")
        self.newer.hide()
        layout.addWidget(self.newer)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.values: dict[str, QLabel] = {}
        self.rows: dict[str, tuple[QLabel, QLabel]] = {}
        for index, (key, caption) in enumerate(
            (
                ("timestamp", t("UTC timestamp")),
                ("source", t("Source interface")),
                ("path", t("Path")),
                ("summary", t("Comment / summary")),
                ("group", t("Destination group")),
                ("event", t("Event code")),
                ("severity", t("Severity level")),
                ("areas", t("Area codes")),
            )
        ):
            caption_label = label(caption, "muted")
            value_label = label("-", None, wrap=True, selectable=True)
            grid.addWidget(caption_label, index, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, index, 1)
            self.values[key] = value_label
            self.rows[key] = (caption_label, value_label)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        from app.desktop.widgets.map_view import MapView

        self.map = MapView(self, interactive=False, show_zoom_control=False)
        self.map.setMinimumHeight(220)
        layout.addWidget(self.map, 1)
        self.no_position = label("", "muted")
        self.no_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.no_position)
        layout.addWidget(label(t("Raw APRS frame"), "muted"))
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setProperty("role", "log")
        self.raw.setFixedHeight(64)
        layout.addWidget(self.raw)
        actions = QHBoxLayout()
        self.copy_button = QPushButton(t("Copy frame"))
        self.copy_button.setProperty("variant", "secondary")
        self.copy_button.clicked.connect(self._copy)
        self.map_button = QPushButton(t("Open on map"))
        self.map_button.setProperty("variant", "secondary")
        self.map_button.clicked.connect(self._open_map)
        self.alert_button = QPushButton(t("Show alert"))
        self.alert_button.setProperty("variant", "secondary")
        self.alert_button.clicked.connect(self._open_alert)
        close_button = QPushButton(t("Close"))
        close_button.clicked.connect(self.close)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.map_button)
        actions.addWidget(self.alert_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self._newer_timer = QTimer(self)
        self._newer_timer.setSingleShot(True)
        self._newer_timer.timeout.connect(self.newer.hide)

    def show_frame(self, frame: dict[str, Any], *, play_sound: bool, newer: bool = False) -> None:
        from app.desktop.map_layers import alert_area_shapes, parse_float, symbol_overlay
        from app.desktop.widgets.map_view import MapMarker

        self.frame = frame
        data = popup_frame_data(frame)
        alarm_group_popup = frame.get("alert_popup_kind") == "alarm_group"
        self.title.setText(t("APRS alert received") if alarm_group_popup else t("Emergency frame received"))
        self.setWindowTitle(self.title.text())
        colors = current_colors()
        self.icon_label.setPixmap(icon_pixmap("car-emergency", colors.color("danger"), 34))
        self.callsign.setText(str(data.get("callsign") or frame.get("display_callsign") or frame.get("source") or "-"))
        if frame.get("alert_muted"):
            self.status.set_badge(t("Alert muted"), "neutral")
        else:
            self.status.set_badge(t("Active APRS alert") if alarm_group_popup else t("Active emergency"), "danger")
        source = " · ".join(str(part) for part in (data.get("source_interface") or frame.get("source"), data.get("source_port")) if part)
        self._set("timestamp", format_utc(data.get("timestamp_utc") or frame.get("timestamp")))
        self._set("source", source or "-")
        self._set("path", data.get("path") or "-")
        self._set("summary", data.get("summary") or data.get("comment") or "-")
        area_codes = [str(code).strip() for code in (data.get("area_codes") or []) if str(code).strip()]
        self._set_optional("group", data.get("destination_group") if alarm_group_popup else "")
        self._set_optional("event", data.get("event_code") if alarm_group_popup else "")
        self._set_optional("severity", data.get("severity_level") if alarm_group_popup else "")
        self._set_optional("areas", ", ".join(area_codes) if alarm_group_popup else "")
        self.raw.setPlainText(str(data.get("raw_frame") or frame.get("line") or "").strip())
        self.alert_button.setVisible(bool(frame.get("alert_id") or frame.get("alert_href")))
        latitude = parse_float(data.get("latitude"))
        longitude = parse_float(data.get("longitude"))
        self.map_button.setEnabled(latitude is not None and longitude is not None)
        areas = data.get("area_feature_collection") if alarm_group_popup else None
        area_shapes = alert_area_shapes(areas, fill_opacity=0.22, width=3.0) if isinstance(areas, dict) else []
        self.map.reload_tile_source()
        if area_shapes:
            self.map.set_layer("areas", area_shapes)
            self.map.set_markers([])
            points = [point for shape in area_shapes for ring in shape.rings for point in ring]
            self.map.show()
            self.no_position.hide()
            QTimer.singleShot(0, lambda: self.map.fit_points(points, padding=18, max_zoom=10))
        elif latitude is not None and longitude is not None:
            self.map.set_layer("areas", [])
            symbol = str(data.get("symbol") or "")
            from app.desktop.icons import symbol_icon_for

            icon_path = symbol_icon_for(symbol[:1], symbol[1:2]) if len(symbol) >= 2 else ""
            self.map.set_markers(
                [MapMarker(key="emergency", latitude=latitude, longitude=longitude, label=self.callsign.text(),
                           icon_path=icon_path, overlay=symbol_overlay(symbol[:1]))]
            )
            self.map.set_view(latitude, longitude, 13)
            self.map.show()
            self.no_position.hide()
        else:
            self.map.hide()
            self.no_position.setText(t("No map area available") if alarm_group_popup else t("No position available in this frame"))
            self.no_position.show()
        if newer:
            self.newer.show()
            self._newer_timer.start(3000)
        if play_sound and not frame.get("alert_muted") and ctx.alert_sound_enabled():
            sound.play_alert(loop=True)
        else:
            sound.stop_alert()
        self.show()
        self.raise_()
        self.activateWindow()

    def _set(self, key: str, value: Any) -> None:
        self.values[key].setText(str(value if value not in (None, "") else "-"))

    def _set_optional(self, key: str, value: Any) -> None:
        text = str(value if value is not None else "").strip()
        caption, output = self.rows[key]
        output.setText(text or "-")
        caption.setVisible(bool(text))
        output.setVisible(bool(text))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.raw.toPlainText())

    def _open_map(self) -> None:
        from app.desktop.map_layers import parse_float

        data = popup_frame_data(self.frame or {})
        latitude = parse_float(data.get("latitude"))
        longitude = parse_float(data.get("longitude"))
        if latitude is None or longitude is None:
            return
        self.watcher.context.go("map", {"latitude": latitude, "longitude": longitude, "zoom": 15})
        window = self.watcher.context.main_window
        if window is not None:
            window.show_normal()

    def _open_alert(self) -> None:
        frame = self.frame or {}
        alert_id = frame.get("alert_id")
        if alert_id:
            self.watcher.context.go("alert-detail", int(alert_id))
        else:
            self.watcher.context.go("alerts")
        window = self.watcher.context.main_window
        if window is not None:
            window.show_normal()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        sound.stop_alert()
        self.watcher.dismissed(self.frame)
        super().closeEvent(event)


class AlertWatcher(QObject):
    """Polls the alert notification snapshot like the web alert SSE stream."""

    def __init__(self, context: ctx.AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.context = context
        # Insertion-ordered (dict keys) so trimming keeps the most recent signatures.
        self.handled: dict[str, None] = {}
        self.dismissed_signature = ""
        self.current_signature = ""
        self.dialog: EmergencyDialog | None = None
        self._last_token: Any = None
        self._loader = SerialLoader(self._load, self._apply, self)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._loader.request)
        self._timer.start(ALERT_POLL_MS)
        self._loader.request()

    def stop(self) -> None:
        self._timer.stop()
        sound.stop_alert()

    def refresh_texts(self) -> None:
        if self.dialog is not None and not self.dialog.isVisible():
            self.dialog.deleteLater()
            self.dialog = None

    def _load(self) -> dict[str, Any] | None:
        from app.db import connection_scope
        from app.services.content import alert_notification_change_token, alert_notification_snapshot

        with connection_scope():  # every 1.5 s: one connection instead of one per query
            token = alert_notification_change_token()
            if token == self._last_token:
                return None
            return {"token": token, "snapshot": alert_notification_snapshot()}

    def _apply(self, result: dict[str, Any] | None) -> None:
        if not result:
            return
        self._last_token = result.get("token")
        frames = list((result.get("snapshot") or {}).get("frames") or [])
        frame = select_newest_popup_frame(frames, self.handled)
        if frame is None:
            return
        signature = emergency_signature(frame)
        visible = self.dialog is not None and self.dialog.isVisible()
        if visible and signature == self.current_signature:
            return
        if not visible and signature == self.dismissed_signature:
            return
        newer = visible and bool(self.current_signature) and signature != self.current_signature
        self.dismissed_signature = ""
        self.current_signature = signature
        self.handled[signature] = None
        if len(self.handled) > 200:
            self.handled = dict.fromkeys(list(self.handled)[-200:])
        if self.dialog is None:
            self.dialog = EmergencyDialog(self, self.context.main_window)
        self.dialog.show_frame(frame, play_sound=True, newer=newer)
        window = self.context.main_window
        tray = getattr(window, "tray", None)
        if tray is not None and (window is None or not window.isVisible()):
            tray.showMessage(self.dialog.title.text(), self.dialog.callsign.text(), QSystemTrayIcon.MessageIcon.Critical, 10000)

    def dismissed(self, frame: dict[str, Any] | None) -> None:
        self.dismissed_signature = self.current_signature

    def open_frame(self, frame: dict[str, Any]) -> bool:
        """Show a given emergency/alert frame (web: window.aprsboxOpenEmergencyModal)."""
        if not isinstance(frame, dict) or not (frame.get("emergency") or frame.get("alert_popup")):
            return False
        self.dismissed_signature = ""
        self.current_signature = emergency_signature(frame)
        if self.dialog is None:
            self.dialog = EmergencyDialog(self, self.context.main_window)
        self.dialog.show_frame(frame, play_sound=not frame.get("alert_muted"))
        return True
