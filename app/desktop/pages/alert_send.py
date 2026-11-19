"""Send alarm page (web: templates/alert_send.html + static/js/alerts-compose.js)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout

from app.datetime_utils import format_display_datetime
from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.tasks import run_async
from app.desktop.widgets.common import (
    Card,
    FlashMessage,
    FormGrid,
    Page,
    button,
    combo_value,
    icon_button,
    key_value_grid,
    label,
    set_combo_options,
    set_combo_value,
    text_edit,
)


def normalized_comment_length(text: str) -> int:
    """Length the backend will see (web: normalizedCommentLength)."""
    value = re.sub(r"[\r\n]", " ", str(text or ""))
    value = unicodedata.normalize("NFKD", value)
    value = re.sub(r"[^\x00-\x7F]", "", value).strip()
    value = re.sub(r"\s+", " ", value).replace("|", "/").replace("{", "(")
    return len(value)


def area_label(area: dict[str, Any] | None) -> str:
    if not area:
        return ""
    parent = f" · {area.get('parent')}" if area.get("parent") else ""
    return f"{area.get('name')} — {area.get('code')}{parent}"


class ConfirmAlarmDialog(QDialog):
    def __init__(self, preview: dict[str, Any], group: dict[str, Any], parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("Confirm alarm transmission"))
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(label(t("Confirm alarm transmission"), "h2"))
        hazard = next((item for item in group.get("hazard_options") or [] if item.get("code") == preview.get("event_family")), None)
        level = next((item for item in group.get("level_options") or []
                      if str(item.get("value")) == str(preview.get("severity_level"))), None)
        hazard_text = (hazard or {}).get("translated_label") or (hazard or {}).get("label") or preview.get("event_family")
        level_text = (level or {}).get("translated_label") or (level or {}).get("label") or preview.get("severity_level")
        layout.addWidget(key_value_grid([
            (t("Target"), preview.get("target_group")),
            (t("Area"), area_label(preview.get("area"))),
            (t("Hazard"), f"{hazard_text} — {level_text}"),
            (t("Valid until"), format_display_datetime(preview.get("valid_until"))),
            (t("Repeat every"), tf("{count} minutes", count=preview.get("repeat_interval_minutes"))),
            (t("Path") + " (RF)", preview.get("tx_path") or t("Direct (no path)")),
            (t("Content"), preview.get("comment") or "-"),
            (t("Frames"), tf("{count} frames", count=preview.get("parts_total"))),
        ]))
        self.technical_button = button(t("Technical APRS preview"), variant="secondary", icon_name="code-tags",
                                       on_click=self._toggle_technical)
        layout.addWidget(self.technical_button)
        self.technical = QPlainTextEdit("\n".join(preview.get("technical_frames") or []))
        self.technical.setReadOnly(True)
        self.technical.setProperty("role", "log")
        self.technical.hide()
        layout.addWidget(self.technical, 1)
        self.error = FlashMessage()
        layout.addWidget(self.error)
        layout.addStretch(1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(button(t("Back"), variant="secondary", on_click=self.reject))
        self.send_button = button(t("Send"), icon_name="send", on_click=self.accept)
        footer.addWidget(self.send_button)
        layout.addLayout(footer)

    def _toggle_technical(self) -> None:
        self.technical.setVisible(not self.technical.isVisible())


class AlertSendPage(Page):
    page_key = "alert-send"

    def build(self) -> None:
        self.compose: dict[str, Any] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.areas: list[dict[str, Any]] = []
        self.last_preview: dict[str, Any] | None = None
        self._preview_sequence = 0
        self._area_request = 0

        card = Card(t("Send alarm"), t("Send a CAWF alarm."))
        card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/alerts")))
        self.empty_label = label(t("No fully supported configured alarm groups are available."), "muted", wrap=True)
        card.add(self.empty_label)
        self.tx_error = FlashMessage()
        card.add(self.tx_error)
        grid = FormGrid(2)
        from PySide6.QtWidgets import QComboBox

        self.target = QComboBox()
        self.area = QComboBox()
        self.area.setMinimumContentsLength(30)
        self.hazard = QComboBox()
        self.level = QComboBox()
        self.repeat = QComboBox()
        self.validity = QComboBox()
        self.path = QComboBox()
        grid.add_field("target_group", t("Target"), self.target)
        grid.add_field("area_code", t("Area"), self.area)
        grid.add_field("event_family", t("Hazard"), self.hazard)
        grid.add_field("severity_level", t("Severity level"), self.level)
        grid.add_field("repeat_interval_minutes", t("Repeat every"), self.repeat)
        grid.add_field("validity_hours", t("Validity"), self.validity)
        grid.add_field("tx_path", t("Path") + " (RF)", self.path)
        self.area_status = label("", "small", wrap=True)
        grid.add_widget(self.area_status, span=1)
        self.comment = text_edit("", placeholder=t("Optional alarm comment"), height=70)
        grid.add_field("comment", t("Content"), self.comment, span=2)
        self.form = grid
        card.add(grid)
        counters = QHBoxLayout()
        self.characters = label(t("Select an area to calculate the limit."), "small")
        self.parts = label("", "muted")
        counters.addWidget(self.characters)
        counters.addWidget(self.parts)
        counters.addStretch(1)
        card.add(counters)
        actions = QHBoxLayout()
        self.status = label("", "error", wrap=True)
        actions.addWidget(self.status, 1)
        actions.addWidget(button(t("Back"), variant="secondary", icon_name="arrow-left", on_click=lambda: self.navigate("alerts")))
        self.send_button = button(t("Send alarm"), icon_name="alarm-light-outline", on_click=self._send)
        actions.addWidget(self.send_button)
        card.add(actions)
        self.body.addWidget(card)
        self.body.addStretch(1)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(160)
        self.preview_timer.timeout.connect(self._request_preview)
        self.target.currentIndexChanged.connect(lambda _index: self._target_changed())
        self.area.currentIndexChanged.connect(lambda _index: self._area_changed())
        for widget in (self.hazard, self.level, self.repeat, self.validity, self.path):
            widget.currentIndexChanged.connect(lambda _index: self.preview_timer.start())
        self.comment.textChanged.connect(self._comment_changed)
        self._loaded = False

    # -------------------------------------------------------------- loading
    def refresh(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        run_async(self._load_compose, self._apply_compose, lambda exc: self.status.setText(str(exc)), owner=self)

    def deactivate(self) -> None:
        super().deactivate()
        # Reload groups and limits next time the page is opened.
        self._loaded = False

    @staticmethod
    def _load_compose() -> dict[str, Any]:
        from app.i18n import get_app_language, get_translator
        from app.services.own_alerts import get_own_alert_compose_context

        compose = get_own_alert_compose_context()
        translate = get_translator(get_app_language())
        for group in compose.get("groups") or []:
            for key in ("event_options", "hazard_options", "level_options"):
                for option in group.get(key) or []:
                    option["translated_label"] = translate(option.get("label"))
        return compose

    def _apply_compose(self, compose: dict[str, Any]) -> None:
        self.compose = compose or {}
        groups = list(self.compose.get("groups") or [])
        self.groups = {str(group.get("group")): group for group in groups}
        has_groups = bool(groups)
        self.empty_label.setVisible(not has_groups)
        self.form.setVisible(has_groups)
        tx_available = bool(self.compose.get("tx_available"))
        if has_groups and not tx_available:
            self.tx_error.error(t(self.compose.get("tx_error") or "TX is not available."))
        else:
            self.tx_error.hide()
        self.send_button.setEnabled(has_groups and tx_available)
        current_target = combo_value(self.target) if self.target.count() else None
        set_combo_options(self.target, [(name, name) for name in self.groups], current_target, translate=False)
        set_combo_options(self.repeat, [(minutes, tf("{count} minutes", count=minutes)) for minutes in self.compose.get("repeat_intervals") or []],
                          self.compose.get("default_repeat_interval"), translate=False)
        set_combo_options(self.validity, [(hours, t("1 hour") if hours == 1 else tf("{count} hours", count=hours))
                                          for hours in self.compose.get("validity_hours") or []],
                          self.compose.get("default_validity_hours"), translate=False)
        set_combo_options(self.path, [(item.get("value"), t(item.get("label") or item.get("value") or ""))
                                      for item in self.compose.get("rf_path_options") or []],
                          self.compose.get("default_rf_path"), translate=False)
        if has_groups:
            self._target_changed()

    def _target_changed(self) -> None:
        group = self.groups.get(str(combo_value(self.target) or ""), {})
        hazard = combo_value(self.hazard) if self.hazard.count() else None
        level = combo_value(self.level) if self.level.count() else "2"
        set_combo_options(self.hazard, [(item.get("code"), item.get("translated_label") or item.get("label"))
                                        for item in group.get("hazard_options") or []], hazard, translate=False)
        set_combo_options(self.level, [(item.get("value"), item.get("translated_label") or item.get("label"))
                                       for item in group.get("level_options") or []], level, translate=False)
        self._load_areas()

    def _load_areas(self) -> None:
        group_name = str(combo_value(self.target) or "")
        self._area_request += 1
        request = self._area_request
        self.areas = []
        self.area.setEnabled(False)
        set_combo_options(self.area, [("", t("Loading areas..."))], "", translate=False)
        self.area_status.setText(t("Loading areas..."))
        set_prop_error(self.area_status, False)
        self._clear_preview()
        from app.services.own_alerts import get_own_alert_area_options

        def done(payload: dict[str, Any]) -> None:
            if request != self._area_request:
                return
            self.areas = list(payload.get("areas") or [])
            options = [("", t("Select an area"))] + [(area.get("code"), area_label(area)) for area in self.areas]
            default = str(payload.get("default_area_code") or "")
            matched = bool(default) and any(area.get("code") == default for area in self.areas)
            set_combo_options(self.area, options, default if matched else "", translate=False)
            self.area.setEnabled(bool(self.areas))
            if matched:
                self.area_status.setText(area_label(self._selected_area()))
            else:
                self.area_status.setText(t("Own station position does not match an area. Select one manually.")
                                         if payload.get("station_position_known")
                                         else t("Own station position is unknown. Select an area manually."))
            self.preview_timer.start()

        def failed(error: BaseException) -> None:
            if request != self._area_request:
                return
            set_combo_options(self.area, [("", t("Areas could not be loaded from GeoJSON."))], "", translate=False)
            self.area_status.setText(t(str(error) or "Areas could not be loaded from GeoJSON."))
            set_prop_error(self.area_status, True)

        run_async(lambda: get_own_alert_area_options(group_name), done, failed, owner=self)

    def _selected_area(self) -> dict[str, Any] | None:
        code = str(combo_value(self.area) or "")
        return next((area for area in self.areas if area.get("code") == code), None)

    def _area_changed(self) -> None:
        if combo_value(self.area):
            self.area_status.setText(area_label(self._selected_area()))
            set_prop_error(self.area_status, False)
        self.preview_timer.start()

    # -------------------------------------------------------------- preview
    def _payload(self) -> dict[str, Any]:
        def number(widget: Any) -> int:
            try:
                return int(combo_value(widget) or 0)
            except (TypeError, ValueError):
                return 0

        return {
            "target_group": str(combo_value(self.target) or ""),
            "area_code": str(combo_value(self.area) or ""),
            "event_family": str(combo_value(self.hazard) or ""),
            "severity_level": number(self.level),
            "validity_hours": number(self.validity),
            "comment": self.comment.toPlainText(),
            "repeat_interval_minutes": number(self.repeat),
            "tx_path": str(combo_value(self.path) or ""),
        }

    def _clear_preview(self, message: str = "") -> None:
        self.last_preview = None
        self.characters.setText(message)
        set_prop_error(self.characters, False)
        self.parts.setText("")

    def _request_preview(self, on_ready: Any = None) -> None:
        payload = self._payload()
        if not payload["target_group"] or not payload["area_code"] or not payload["event_family"] or not payload["severity_level"]:
            self._clear_preview(t("Select an area"))
            if on_ready is not None:
                on_ready(None)
            return
        self._preview_sequence += 1
        sequence = self._preview_sequence
        from app.services.own_alerts import preview_own_alert

        def done(preview: dict[str, Any]) -> None:
            if sequence != self._preview_sequence:
                return
            self.last_preview = preview
            self._update_counter()
            if on_ready is not None:
                on_ready(preview)

        def failed(error: BaseException) -> None:
            if sequence != self._preview_sequence:
                return
            self._clear_preview(t(str(error) or "Alarm preview could not be generated."))
            set_prop_error(self.characters, True)
            if on_ready is not None:
                on_ready(None)

        run_async(lambda: preview_own_alert(payload), done, failed, owner=self)

    def _comment_changed(self) -> None:
        self._update_counter()
        self.preview_timer.start()

    def _update_counter(self) -> None:
        preview = self.last_preview or {}
        per_frame = int(preview.get("comment_capacity_per_frame") or 0)
        maximum = int(preview.get("comment_max_capacity") or 0)
        if not per_frame or not maximum:
            return
        length = normalized_comment_length(self.comment.toPlainText())
        max_parts = max(1, maximum // per_frame)
        parts = min(max_parts, max(1, -(-length // per_frame)))
        remaining = max(0, per_frame * parts - length)
        set_prop_error(self.characters, False)
        self.characters.setText(tf("Remaining {count} characters", count=remaining))
        self.parts.setText(tf("{count} frames", count=parts))

    # ----------------------------------------------------------------- send
    def _send(self) -> None:
        self.status.setText("")
        payload = self._payload()
        if not payload["area_code"]:
            self.status.setText(t("Select an area"))
            return

        def ready(preview: dict[str, Any] | None) -> None:
            if not preview:
                self.status.setText(t("Alarm preview could not be generated."))
                return
            group = self.groups.get(str(preview.get("target_group") or payload["target_group"]), {})
            dialog = ConfirmAlarmDialog(preview, group, self)
            while dialog.exec() == QDialog.DialogCode.Accepted:
                from app.services.own_alerts import create_own_alert

                try:
                    create_own_alert(payload)
                except (ValueError, TypeError) as exc:
                    dialog.error.error(t(str(exc)))
                    continue
                self.comment.clear()
                self.context.notify_data_changed("alerts")
                self.navigate("alerts", {"flash": t("Alarm queued for transmission."), "flash_success": True})
                return

        self._request_preview(ready)

    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict) and argument.get("group") and self.target.count():
            set_combo_value(self.target, argument["group"])


def set_prop_error(widget: Any, error: bool) -> None:
    from app.desktop.widgets.common import set_prop

    set_prop(widget, "role", "error" if error else "small")
