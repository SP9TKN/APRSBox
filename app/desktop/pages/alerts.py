"""Alerts list page (web: templates/alerts.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon_pixmap
from app.desktop.routes import route_for_href
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors
from app.desktop.widgets.common import Card, Page, button, combo, combo_value, confirm, icon_button, label
from app.desktop.widgets.crud import RowActionBar
from app.desktop.widgets.table import Column, DataTable

MUTE_OPTIONS = [("1h", "Mute for 1 hour"), ("4h", "Mute for 4 hours"), ("24h", "Mute for 24 hours"), ("indefinite", "Mute indefinitely")]


def mute_combo() -> Any:
    from PySide6.QtWidgets import QComboBox

    widget = combo(MUTE_OPTIONS, "1h")
    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    return widget


def alert_category_color(alert: dict[str, Any]) -> QColor:
    """Web: .alert-category-badge-{emergency,level-1,level-2,level-3,unknown}."""
    colors = current_colors()
    if not alert.get("destination_group"):
        return colors.color("danger")
    level = alert.get("severity_level")
    if level == 1:
        return QColor("#ffd43b" if colors.is_dark else "#8a6500")
    if level == 2:
        return QColor("#ff922b" if colors.is_dark else "#a84f00")
    if level == 3:
        return colors.color("danger")
    return colors.color("muted")


def alert_station_label(alert: dict[str, Any]) -> str:
    return str(alert.get("source_callsign") or "")


def own_station_callsign() -> str:
    from app.services.content import get_station_settings

    station = get_station_settings(include_tx_runtime=False)
    callsign = str(station.get("callsign") or "").strip().upper()
    ssid = str(station.get("ssid") or "").strip()
    if callsign and ssid and ssid != "0":
        callsign = f"{callsign}-{ssid}"
    return callsign


def decorate_alerts(items: list[dict[str, Any]], station_callsign: str) -> list[dict[str, Any]]:
    """Adds can_cancel_protocol / protocol_cancel_label like routers/pages.alerts_page."""
    for alert in items:
        source_matches = bool(station_callsign) and str(alert.get("source_callsign") or "").strip().upper() == station_callsign
        alert["can_cancel_protocol"] = bool(
            source_matches and alert.get("destination_group") and alert.get("logical_alert_id") and alert.get("area_codes")
        )
        alert["protocol_cancel_label"] = (
            f"{str(alert.get('logical_alert_id') or '').strip().upper()} · {str(alert.get('destination_group') or '').strip().upper()}"
        ).strip(" ·")
    return items


def cancel_protocol_alert(parent: Any, alert: dict[str, Any]) -> tuple[bool, str] | None:
    text = "\n\n".join([
        t("This stops repeats and sends a protocol CANCEL with the same logical ID."),
        str(alert.get("protocol_cancel_label") or ""),
    ])
    if not confirm(parent, t("Cancel alarm"), text, danger=True, confirm_text=t("Cancel alarm")):
        return None
    from app.services.own_alerts import cancel_station_aprs_alert

    success, message = cancel_station_aprs_alert(int(alert["id"]))
    return success, ("Own alarm cancelled." if success else str(message or "Operation failed."))


class AlertsPage(Page):
    page_key = "alerts"

    def build(self) -> None:
        self.page_number = 1
        self.items: list[dict[str, Any]] = []
        manage = self.can_edit
        card = Card(t("Alerts"))
        card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/alerts")))
        if manage:
            card.add_action(button(t("Send alarm"), icon_name="alarm-light-outline", on_click=lambda: self.navigate("alert-send")))
        card.add(self.flash)
        columns = []
        if manage:
            columns.append(Column("select", "", width=34, checkable=True, display=lambda item: ""))
        columns += [
            Column("source_callsign", t("Source"), width=150, bold=lambda item: True),
            Column("event_code", t("Category"), stretch=True,
                   display=lambda item: str(item.get("event_code") or item.get("alert_type") or ""),
                   icon=lambda item: icon_pixmap(str(item.get("event_icon") or "alert-outline.svg").removesuffix(".svg"),
                                                 alert_category_color(item), 18),
                   foreground=alert_category_color,
                   secondary=self._category_note),
            Column("area_count", t("Area count"), width=140,
                   display=lambda item: str(item.get("area_count") or 0) if item.get("destination_group") else "-"),
            Column("relevant_time", t("Expiry / last received"), width=200, display=self._relevant_time,
                   tooltip=lambda item: t("Expires at") if item.get("destination_group") and item.get("expires_at_label") else t("Last received")),
        ]
        self.table = DataTable(columns, row_height=44, sortable=False)
        self.table.set_empty_text(t("No alerts received yet."))
        self.table.setMinimumHeight(380)
        self.table.row_activated.connect(self._open_detail)
        card.add(self.table, 1)

        self.actions = RowActionBar(self.table)
        self.actions.add(t("Details"), "eye-outline", self._open_modal)
        self.actions.add(t("Alert details"), "file-document-alert-outline", self._open_detail)
        self.actions.add(t("Station"), "account-outline", self._open_station,
                         enabled_for=lambda item: route_for_href((item.get("related_entity") or {}).get("detail_href")) is not None)
        if manage:
            self.actions.add(t("Cancel alarm"), "alarm-off", self._cancel_protocol, variant="danger",
                             enabled_for=lambda item: bool(item.get("can_cancel_protocol")))
            self.mute_combo = mute_combo()
            self.actions._layout.insertWidget(self.actions._layout.count() - 1, self.mute_combo)
            self.actions.add(t("Mute"), "volume-off", self._mute)
            self.actions.add(t("Cancel mute"), "volume-high", self._unmute, enabled_for=lambda item: bool(item.get("muted")))
            self.actions.add(t("Delete"), "delete-outline", self._delete, variant="danger")
        card.add(self.actions)

        footer = QHBoxLayout()
        if manage:
            self.select_all = QCheckBox(t("Select all"))
            self.select_all.toggled.connect(self.table.source.set_all_checked)
            footer.addWidget(self.select_all)
            footer.addWidget(button(t("Delete selected"), variant="danger", icon_name="delete-sweep-outline", on_click=self._delete_selected))
        self.summary = label("", "muted")
        footer.addWidget(self.summary)
        footer.addStretch(1)
        self.previous_button = button(t("Previous"), variant="secondary", icon_name="chevron-left", on_click=self._previous)
        self.next_button = button(t("Next"), variant="secondary", icon_name="chevron-right", on_click=self._next)
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        card.add(footer)
        self.body.addWidget(card, 1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000

    # ---------------------------------------------------------------- rows
    @staticmethod
    def _category_note(item: dict[str, Any]) -> str:
        if not item.get("muted"):
            return ""
        if item.get("muted_indefinitely"):
            return t("Alert muted indefinitely")
        return f"{t('Muted until')} {item.get('muted_until_label') or ''}"

    @staticmethod
    def _relevant_time(item: dict[str, Any]) -> str:
        if item.get("destination_group"):
            return str(item.get("expires_at_label") or "-")
        return str(item.get("last_seen_label") or "-")

    # -------------------------------------------------------------- loading
    def open_argument(self, argument: Any) -> None:
        if not isinstance(argument, dict):
            return
        try:
            self.page_number = max(1, int(argument.get("page") or 1))
        except (TypeError, ValueError):
            self.page_number = 1
        if argument.get("flash"):
            message = t(str(argument["flash"]))
            if argument.get("flash_success", True) not in (False, "0", "false"):
                self.flash.success(message)
            else:
                self.flash.error(message)

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.alerts import list_alerts

        page_number = self.page_number
        with connection_scope():
            data = list_alerts(page=page_number)
            decorate_alerts(data.get("items") or [], own_station_callsign())
        return data

    def _apply(self, data: dict[str, Any]) -> None:
        self.items = list(data.get("items") or [])
        self.page_number = int(data.get("page") or 1)
        self.table.set_rows(self.items)  # keeps the checked rows that are still listed
        self.actions.sync()
        self.summary.setText(
            tf("Page {page} of {pages}", page=data.get("page") or 1, pages=data.get("total_pages") or 1)
            + " · " + tf("{count} alerts", count=data.get("total") or 0)
        )
        self.previous_button.setEnabled(bool(data.get("has_previous")))
        self.next_button.setEnabled(bool(data.get("has_next")))

    def _previous(self) -> None:
        self.page_number = max(1, self.page_number - 1)
        self.refresh()

    def _next(self) -> None:
        self.page_number += 1
        self.refresh()

    # -------------------------------------------------------------- actions
    def _open_modal(self, item: dict[str, Any]) -> None:
        window = self.context.main_window
        watcher = getattr(window, "alert_watcher", None)
        frame = item.get("modal_frame")
        if watcher is None or not frame or not watcher.open_frame(frame):
            self._open_detail(item)

    def _open_detail(self, item: dict[str, Any]) -> None:
        self.navigate("alert-detail", int(item["id"]))

    def _open_station(self, item: dict[str, Any]) -> None:
        route = route_for_href((item.get("related_entity") or {}).get("detail_href"))
        if route is not None:
            self.navigate(*route)

    def _cancel_protocol(self, item: dict[str, Any]) -> None:
        result = cancel_protocol_alert(self, item)
        if result is None:
            return
        success, message = result
        (self.flash.success if success else self.flash.error)(t(message))
        self.refresh()

    def _mute(self, item: dict[str, Any]) -> None:
        from app.services.alerts import mute_alert

        try:
            mute_alert(int(item["id"]), str(combo_value(self.mute_combo) or "1h"))
        except ValueError:
            self.flash.error(t("Unsupported mute duration."))
            return
        self.flash.success(t("Alert muted."))
        self.context.notify_data_changed("alerts")
        self.refresh()

    def _unmute(self, item: dict[str, Any]) -> None:
        from app.services.alerts import unmute_alert

        unmute_alert(int(item["id"]))
        self.flash.success(t("Alert unmuted."))
        self.context.notify_data_changed("alerts")
        self.refresh()

    def _delete(self, item: dict[str, Any]) -> None:
        if not confirm(self, t("Delete"), t("Delete this alert? Original frames remain in Traffic Monitor."), danger=True,
                       confirm_text=t("Delete")):
            return
        from app.services.alerts import delete_alert

        delete_alert(int(item["id"]))
        self.flash.success(t("Alert deleted. Original frames remain in Traffic Monitor."))
        self.context.notify_data_changed("alerts")
        self.refresh()

    def _delete_selected(self) -> None:
        rows = self.table.source.checked_rows()
        if not rows:
            self.flash.error(t("No alerts selected."))
            return
        if not confirm(self, t("Delete selected"), t("Delete selected alerts? Original frames remain in Traffic Monitor."), danger=True,
                       confirm_text=t("Delete")):
            return
        from app.services.alerts import delete_alerts

        deleted = delete_alerts([int(row["id"]) for row in rows])
        self.select_all.setChecked(False)
        if deleted <= 0:
            self.flash.error(t("No alerts selected."))
        else:
            self.flash.success(t("Selected alerts deleted. Original frames remain in Traffic Monitor."))
        self.context.notify_data_changed("alerts")
        self.refresh()
