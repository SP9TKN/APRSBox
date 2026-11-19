"""Objects / Items page (web: templates/section.html for the objects and items sections)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QTabBar, QVBoxLayout, QWidget

from app.desktop.help import show_help
from app.desktop.i18n import t
from app.desktop.icons import icon_pixmap, symbol_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.activation import ActivationScheduleEditor
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
    line_edit,
    set_combo_value,
    tone_color,
)
from app.desktop.widgets.crud import AsciiTextField, tx_log_table
from app.desktop.widgets.pickers import LocationPickerDialog, SymbolPicker
from app.desktop.widgets.table import Column, DataTable

INTERVAL_OPTIONS = [(str(value), f"{value}m") for value in (5, 10, 15, 30, 45, 60)]
LIFETIME_OPTIONS = [("temporary", "Temporary"), ("permanent", "Permanent")]

SLUG_TEXT = {
    "objects": {
        "list_title": "Object List",
        "create_title": "Add Object",
        "edit_title": "Edit Object",
        "name_label": "Object Name",
        "name_placeholder": "VOICE",
        "state_label": "Object Activity",
        "states": [("live", "Live (*)"), ("killed", "Killed (_)")],
        "enabled_label": "Enabled",
        "comment_placeholder": "Local voice repeater",
        "symbol_code": "r",
        "saved": "Object saved.",
        "deleted": "Object deleted.",
        "delete_confirm": "Delete this object?",
        "send_confirm": "Send this object now?",
    },
    "items": {
        "list_title": "Item List",
        "create_title": "Add Item",
        "edit_title": "Edit Item",
        "name_label": "Item Name",
        "name_placeholder": "AID#2",
        "state_label": "Item State",
        "states": [("live", "Live (!)"), ("killed", "Killed (_)")],
        "enabled_label": "Active",
        "comment_placeholder": "First aid station",
        "symbol_code": "A",
        "saved": "Item saved.",
        "deleted": "Item deleted.",
        "delete_confirm": "Delete this item?",
        "send_confirm": "",
    },
}


def _status_icon(active: bool, on_icon: str = "check-circle") -> Any:
    return icon_pixmap(on_icon if active else "close-circle", tone_color("ok" if active else "neutral"), 18)


class PreparedEntityPanel(QWidget):
    """List + form for one of the 'objects' / 'items' sections."""

    def __init__(self, page: "ObjectsPage", slug: str) -> None:
        super().__init__()
        self.page = page
        self.slug = slug
        self.text = SLUG_TEXT[slug]
        self.edit_id: int | None = None
        self.edit_row: dict[str, Any] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        list_card = Card(t(self.text["list_title"]))
        columns = [
            Column("symbol_icon", t("Icon"), width=52, display=lambda item: "",
                   icon=lambda item: symbol_pixmap(item.get("symbol_icon"), item.get("symbol_overlay") or "", 22),
                   tooltip=lambda item: f"{item.get('symbol_table') or ''}{item.get('symbol_code') or ''}"),
            Column("is_enabled", t("Enabled") if slug == "objects" else t("Active"), width=92, display=lambda item: "",
                   icon=lambda item: _status_icon(bool(item.get("is_enabled"))),
                   tooltip=lambda item: t("Enabled") if item.get("is_enabled") else t("Disabled")),
        ]
        if slug == "objects":
            columns.append(
                Column("activation_active_now", t("Active now"), width=118, display=lambda item: "",
                       icon=lambda item: _status_icon(bool(item.get("activation_active_now")), "play-circle"),
                       tooltip=lambda item: str(item.get("activation_summary") or ""))
            )
        columns += [
            Column("activation_short_label", t("Schedule"), width=210, display=lambda item: t(item.get("activation_short_label") or ""),
                   tooltip=lambda item: str(item.get("activation_summary") or "")),
            Column("interval_minutes", t("Every"), width=64, display=lambda item: f"{item.get('interval_minutes')}m"),
            Column("name", t(self.text["name_label"]), width=110, bold=lambda item: True),
            Column("raw_frame_preview", t("Raw Frame Preview"), stretch=True, mono=True,
                   tooltip=lambda item: str(item.get("raw_frame_preview") or "")),
        ]
        self.table = DataTable(columns, row_height=34, sortable=False)
        self.table.set_empty_text(t("No records yet."))
        self.table.setMinimumHeight(240)
        self.table.row_activated.connect(lambda item: self.edit(int(item["id"])))
        self.table.clicked.connect(lambda _index: self._clicked())
        list_card.add(self.table, 1)
        layout.addWidget(list_card)

        self.form_card = Card(t(self.text["create_title"]))
        if slug == "objects":
            self.form_card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/objects")))
        self.flash = FlashMessage()
        self.form_card.add(self.flash)
        grid = FormGrid(3)
        self.name = line_edit("", placeholder=t(self.text["name_placeholder"]), max_length=9)
        grid.add_field("name", t(self.text["name_label"]), self.name)
        if slug == "objects":
            self.lifetime = combo(LIFETIME_OPTIONS, "temporary")
            grid.add_field("lifetime", t("Object Lifetime"), self.lifetime)
        self.state = combo(self.text["states"], "live")
        grid.add_field("state", t(self.text["state_label"]), self.state)
        self.latitude = line_edit("")
        self.longitude = line_edit("")
        grid.add_field("latitude", t("Latitude"), self.latitude)
        grid.add_field("longitude", t("Longitude"), self.longitude)
        location = button(t("Get location"), variant="secondary", icon_name="map-marker-plus-outline", on_click=self._pick_location)
        location_box = QWidget()
        location_layout = QVBoxLayout(location_box)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.addStretch(1)
        location_layout.addWidget(location)
        grid.add_widget(location_box, span=1)
        self.symbol = SymbolPicker()
        grid.add_widget(self.symbol)
        self.interval = combo(INTERVAL_OPTIONS, "30")
        grid.add_field("interval_minutes", t("Send Interval"), self.interval)
        self.form_card.add(grid)
        self.path = line_edit("", max_length=64)
        self.activation = ActivationScheduleEditor(lambda: self.path.text(), lambda: combo_value(self.interval))
        self.path.textChanged.connect(lambda _text: self.activation.sync())
        self.interval.currentIndexChanged.connect(lambda _index: self.activation.sync())
        self.form_card.add(self.activation)
        tail = FormGrid(3)
        tail.add_field("path", t("Path"), self.path)
        self.enabled = checkbox(t(self.text["enabled_label"]))
        tail.add_widget(self.enabled)
        self.comment = AsciiTextField(43, placeholder=t(self.text["comment_placeholder"]))
        tail.add_field("comment", t("Comment"), self.comment, span=3)
        self.form_card.add(tail)
        actions = QHBoxLayout()
        actions.addWidget(button(t("Save"), icon_name="content-save-outline", on_click=self.save))
        self.send_button = button(t("Send now"), variant="secondary", icon_name="send-outline", on_click=self.send_now)
        actions.addWidget(self.send_button)
        self.cancel_button = button(t("Cancel"), variant="secondary", on_click=self.new)
        actions.addWidget(self.cancel_button)
        self.delete_button = button(t("Delete"), variant="danger", icon_name="delete-outline", on_click=self.delete)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.form_card.add(actions)
        layout.addWidget(self.form_card)

        self.log_card: Card | None = None
        if slug == "objects":
            self.log_card = Card(t("Objects TX Log"))
            self.log_table = tx_log_table()
            self.log_card.add(self.log_table)
            layout.addWidget(self.log_card)
        self.new()

    # ---------------------------------------------------------------- data
    def load(self) -> dict[str, Any]:
        from app.services.content import get_section_rows, recent_object_outbound_jobs

        data: dict[str, Any] = {"rows": get_section_rows(self.slug)}
        if self.slug == "objects":
            data["log"] = recent_object_outbound_jobs(limit=20)
        return data

    def apply(self, data: dict[str, Any]) -> None:
        self.table.set_rows(data.get("rows") or [])
        if self.slug == "objects":
            self.log_table.set_rows(data.get("log") or [])
        if self.edit_id is not None:
            self.table.select_key(self.edit_id, scroll=False)

    def _clicked(self) -> None:
        row = self.table.current_row()
        if row is not None and self.page.can_edit:
            self.edit(int(row["id"]))

    # ---------------------------------------------------------------- form
    def new(self) -> None:
        self.edit_id = None
        self.edit_row = None
        self.form_card.set_title(t(self.text["create_title"]))
        self._fill({})
        self.table.clearSelection()

    def edit(self, record_id: int) -> None:
        from app.services.content import get_section_row

        row = get_section_row(self.slug, record_id)
        if row is None:
            self.flash.error(t("Record not found."))
            return
        self.edit_id = record_id
        self.edit_row = row
        self.form_card.set_title(t(self.text["edit_title"]))
        self._fill(row)
        self.flash.hide()

    def _fill(self, row: dict[str, Any]) -> None:
        self.name.setText(str(row.get("name") or ""))
        if self.slug == "objects":
            set_combo_value(self.lifetime, row.get("lifetime") or "temporary")
        set_combo_value(self.state, row.get("state") or "live")
        self.latitude.setText("" if row.get("latitude") is None else str(row.get("latitude")))
        self.longitude.setText("" if row.get("longitude") is None else str(row.get("longitude")))
        self.symbol.set_value(row.get("symbol_table") or "/", row.get("symbol_code") or self.text["symbol_code"], row.get("symbol_overlay") or "")
        set_combo_value(self.interval, str(row.get("interval_minutes") or 30))
        self.path.setText(str(row.get("path") or ""))
        self.enabled.setChecked(bool(row.get("is_enabled")))
        self.comment.set_text(str(row.get("comment") or ""))
        self.activation.load(row)
        editing = bool(row.get("id"))
        self.send_button.setVisible(editing and self.slug == "objects")
        self.cancel_button.setVisible(editing)
        self.delete_button.setVisible(editing)
        for widget in (self.form_card,):
            widget.setEnabled(self.page.can_edit)

    def _pick_location(self) -> None:
        picked = LocationPickerDialog.pick(self, self.latitude.text(), self.longitude.text())
        if picked is not None:
            self.latitude.setText(f"{picked[0]:.5f}")
            self.longitude.setText(f"{picked[1]:.5f}")

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name.text().strip(),
            "state": str(combo_value(self.state) or "live"),
            "latitude": self.latitude.text().strip(),
            "longitude": self.longitude.text().strip(),
            **self.symbol.values(),
            "interval_minutes": str(combo_value(self.interval) or "30"),
            **self.activation.values(),
            "path": self.path.text().strip(),
            "is_enabled": form_flag(self.enabled.isChecked()),
            "comment": self.comment.text().strip(),
        }
        if self.slug == "objects":
            payload["lifetime"] = str(combo_value(self.lifetime) or "temporary")
        return payload

    def save(self) -> None:
        from app.db import fetch_one
        from app.services.content import safe_create_section_row, safe_update_section_row

        payload = self.payload()
        if not payload["name"]:
            self.flash.error(t("This field is required.") + f" ({t(self.text['name_label'])})")
            return
        if self.edit_id is None:
            success, error = safe_create_section_row(self.slug, payload)
            if success:
                table = "aprs_objects" if self.slug == "objects" else "aprs_items"
                created = fetch_one(f"SELECT id FROM {table} WHERE name = ?", (payload["name"],))
                if created is not None:
                    self.edit(int(created["id"]))
        else:
            success, error = safe_update_section_row(self.slug, self.edit_id, payload)
            if success:
                self.edit(self.edit_id)
        if not success:
            self.flash.error(t(error or "Failed to save object."))
            return
        self.flash.success(t(self.text["saved"]))
        self.page.context.notify_data_changed(self.slug)
        self.page.refresh()

    def send_now(self) -> None:
        if self.edit_id is None:
            return
        if not confirm(self, t("Are you sure?"), t(self.text["send_confirm"]), confirm_text=t("Send now")):
            return
        from app.services.content import get_section_row, get_station_settings
        from app.services.outbound import enqueue_object_job

        row = get_section_row(self.slug, self.edit_id)
        if row is None:
            self.flash.error(t("Object not found."))
            return
        success, message = enqueue_object_job(row, get_station_settings(), trigger="manual", force_send=True)
        if success:
            self.flash.success(t(message or "Object queued."))
        else:
            self.flash.error(t(message or "Failed to send object."))
        self.page.refresh()

    def delete(self) -> None:
        if self.edit_id is None:
            return
        if not confirm(self, t("Are you sure?"), t(self.text["delete_confirm"]), danger=True, confirm_text=t("Delete")):
            return
        from app.services.content import delete_section_row

        delete_section_row(self.slug, self.edit_id)
        self.new()
        self.flash.success(t(self.text["deleted"]))
        self.page.context.notify_data_changed(self.slug)
        self.page.refresh()


class ObjectsPage(Page):
    page_key = "objects"

    def build(self) -> None:
        self.tabs = QTabBar()
        self.tabs.addTab(t("Objects"))
        self.tabs.addTab(t("Items"))
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.body.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.panels = {"objects": PreparedEntityPanel(self, "objects"), "items": PreparedEntityPanel(self, "items")}
        for panel in self.panels.values():
            self.body.addWidget(panel)
        self.body.addStretch(1)
        self.panels["items"].hide()
        self.active_slug = "objects"
        self.tabs.currentChanged.connect(self._tab_changed)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000

    def open_argument(self, argument: Any) -> None:
        if not isinstance(argument, dict):
            return
        slug = str(argument.get("slug") or argument.get("tab") or "objects")
        if slug not in self.panels:
            return
        self.tabs.setCurrentIndex(0 if slug == "objects" else 1)
        if argument.get("edit"):
            try:
                self.panels[slug].edit(int(argument["edit"]))
            except (TypeError, ValueError):
                pass

    def _tab_changed(self, index: int) -> None:
        self.active_slug = "items" if index == 1 else "objects"
        self.panels["objects"].setVisible(index == 0)
        self.panels["items"].setVisible(index == 1)
        self.refresh()

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> tuple[str, dict[str, Any]]:
        # Worker thread: only the plain slug attribute is read here.
        slug = self.active_slug
        return slug, self.panels[slug].load()

    def _apply(self, result: tuple[str, dict[str, Any]]) -> None:
        slug, data = result
        self.panels[slug].apply(data)
