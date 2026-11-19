"""Bulletins / Announcements page (web: templates/section.html for the bulletins section)."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtWidgets import QHBoxLayout

from app.desktop.help import show_help
from app.desktop.i18n import t
from app.desktop.icons import icon_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.activation import ActivationScheduleEditor
from app.desktop.widgets.common import (
    Card,
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
from app.desktop.widgets.table import Column, DataTable

KIND_OPTIONS = [("bulletin", "General Bulletin"), ("announcement", "Announcement"), ("group_bulletin", "Group Bulletin")]
INTERVAL_OPTIONS = [(str(value), f"{value}m") for value in (5, 10, 15, 30, 45, 60)]


def _status_icon(active: bool, on_icon: str = "check-circle") -> Any:
    return icon_pixmap(on_icon if active else "close-circle", tone_color("ok" if active else "neutral"), 18)


class BulletinsPage(Page):
    page_key = "bulletins"

    def build(self) -> None:
        self.edit_id: int | None = None
        list_card = Card(t("Bulletins / Announcements List"))
        self.table = DataTable(
            [
                Column("is_enabled", t("Enabled"), width=92, display=lambda item: "",
                       icon=lambda item: _status_icon(bool(item.get("is_enabled"))),
                       tooltip=lambda item: t("Enabled") if item.get("is_enabled") else t("Disabled")),
                Column("activation_active_now", t("Active now"), width=118, display=lambda item: "",
                       icon=lambda item: _status_icon(bool(item.get("activation_active_now")), "play-circle"),
                       tooltip=lambda item: str(item.get("activation_summary") or "")),
                Column("activation_short_label", t("Schedule"), width=210,
                       display=lambda item: t(item.get("activation_short_label") or ""),
                       tooltip=lambda item: str(item.get("activation_summary") or "")),
                Column("interval_minutes", t("Every"), width=64, display=lambda item: f"{item.get('interval_minutes')}m"),
                Column("type_label", t("Type"), width=130, display=lambda item: t(item.get("type_label") or "")),
                Column("target_display", t("Target"), width=100, mono=True),
                Column("raw_frame_preview", t("Raw Frame Preview"), stretch=True, mono=True,
                       tooltip=lambda item: str(item.get("raw_frame_preview") or "")),
            ],
            row_height=34,
            sortable=False,
        )
        self.table.set_empty_text(t("No records yet."))
        self.table.setMinimumHeight(240)
        self.table.row_activated.connect(lambda item: self.edit(int(item["id"])))
        self.table.clicked.connect(lambda _index: self._clicked())
        list_card.add(self.table, 1)
        self.body.addWidget(list_card)

        self.form_card = Card(t("Add Bulletin / Announcement"))
        self.form_card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/bulletins")))
        self.form_card.add(self.flash)
        grid = FormGrid(3)
        self.kind = combo(KIND_OPTIONS, "bulletin")
        self.kind.currentIndexChanged.connect(self._kind_changed)
        grid.add_field("message_kind", t("Type"), self.kind)
        self.code = line_edit("", placeholder="0", max_length=1)
        self.code.textEdited.connect(self._code_edited)
        grid.add_field("bulletin_code", t("Code"), self.code)
        self.group = line_edit("", placeholder="WX", max_length=5)
        self.group.textEdited.connect(self._group_edited)
        grid.add_field("group_name", t("Group"), self.group)
        self.interval = combo(INTERVAL_OPTIONS, "30")
        grid.add_field("interval_minutes", t("Send Interval"), self.interval)
        self.grid = grid
        self.form_card.add(grid)
        self.path = line_edit("", max_length=64)
        self.activation = ActivationScheduleEditor(lambda: self.path.text(), lambda: combo_value(self.interval))
        self.path.textChanged.connect(lambda _text: self.activation.sync())
        self.interval.currentIndexChanged.connect(lambda _index: self.activation.sync())
        self.form_card.add(self.activation)
        tail = FormGrid(3)
        tail.add_field("path", t("Path"), self.path)
        self.enabled = checkbox(t("Enabled"))
        tail.add_widget(self.enabled)
        self.text = AsciiTextField(67, placeholder=t("Net starts at 19:30 UTC"))
        tail.add_field("message_text", t("Message Text"), self.text, span=3)
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
        self.form_card.setEnabled(self.can_edit)
        self.body.addWidget(self.form_card)

        log_card = Card(t("Bulletins TX Log"))
        self.log_table = tx_log_table()
        log_card.add(self.log_table)
        self.body.addWidget(log_card)
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000
        self.new()

    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict) and argument.get("edit"):
            self.edit(int(argument["edit"]))

    # ---------------------------------------------------------------- data
    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.services.content import get_section_rows, recent_bulletin_outbound_jobs

        return {"rows": get_section_rows("bulletins"), "log": recent_bulletin_outbound_jobs(limit=20)}

    def _apply(self, data: dict[str, Any]) -> None:
        self.table.set_rows(data.get("rows") or [])
        self.log_table.set_rows(data.get("log") or [])
        if self.edit_id is not None:
            self.table.select_key(self.edit_id, scroll=False)

    def _clicked(self) -> None:
        row = self.table.current_row()
        if row is not None and self.can_edit:
            self.edit(int(row["id"]))

    # ---------------------------------------------------------------- form
    def _kind_changed(self) -> None:
        kind = str(combo_value(self.kind) or "bulletin")
        self.grid.set_row_visible("group_name", kind == "group_bulletin")
        self.code.setPlaceholderText("A" if kind == "announcement" else "0")
        self._code_edited(self.code.text())

    def _code_edited(self, text: str) -> None:
        announcement = combo_value(self.kind) == "announcement"
        filtered = re.sub(r"[^A-Z]" if announcement else r"[^0-9]", "", text.upper())[:1]
        if filtered != text:
            self.code.setText(filtered)

    def _group_edited(self, text: str) -> None:
        filtered = re.sub(r"[^A-Z0-9]", "", text.upper())[:5]
        if filtered != text:
            self.group.setText(filtered)

    def new(self) -> None:
        self.edit_id = None
        self.form_card.set_title(t("Add Bulletin / Announcement"))
        self._fill({})
        self.table.clearSelection()

    def edit(self, record_id: int) -> None:
        from app.services.content import get_section_row

        row = get_section_row("bulletins", record_id)
        if row is None:
            self.flash.error(t("Bulletin not found."))
            return
        self.edit_id = record_id
        self.form_card.set_title(t("Edit Bulletin / Announcement"))
        self._fill(row)
        self.flash.hide()

    def _fill(self, row: dict[str, Any]) -> None:
        set_combo_value(self.kind, row.get("message_kind") or "bulletin")
        self.code.setText(str(row.get("bulletin_code") or ""))
        self.group.setText(str(row.get("group_name") or ""))
        set_combo_value(self.interval, str(row.get("interval_minutes") or 30))
        self.path.setText(str(row.get("path") or ""))
        self.enabled.setChecked(bool(row.get("is_enabled")))
        self.text.set_text(str(row.get("message_text") or ""))
        self.activation.load(row)
        self._kind_changed()
        editing = bool(row.get("id"))
        for widget in (self.send_button, self.cancel_button, self.delete_button):
            widget.setVisible(editing)

    def payload(self) -> dict[str, Any]:
        return {
            "message_kind": str(combo_value(self.kind) or "bulletin"),
            "bulletin_code": self.code.text().strip(),
            "group_name": self.group.text().strip() if combo_value(self.kind) == "group_bulletin" else "",
            "interval_minutes": str(combo_value(self.interval) or "30"),
            **self.activation.values(),
            "path": self.path.text().strip(),
            "is_enabled": form_flag(self.enabled.isChecked()),
            "message_text": self.text.text().strip(),
        }

    def save(self) -> None:
        from app.db import fetch_one
        from app.services.content import safe_create_section_row, safe_update_section_row

        payload = self.payload()
        if self.edit_id is None:
            success, error = safe_create_section_row("bulletins", payload)
            if success:
                created = fetch_one("SELECT id FROM bulletins ORDER BY id DESC LIMIT 1")
                if created is not None:
                    self.edit(int(created["id"]))
        else:
            success, error = safe_update_section_row("bulletins", self.edit_id, payload)
            if success:
                self.edit(self.edit_id)
        if not success:
            self.flash.error(t(error or "Failed to save bulletin."))
            return
        self.flash.success(t("Bulletin saved."))
        self.refresh()

    def send_now(self) -> None:
        if self.edit_id is None:
            return
        if not confirm(self, t("Are you sure?"), t("Send this bulletin now?"), confirm_text=t("Send now")):
            return
        from app.services.content import get_section_row, get_station_settings
        from app.services.outbound import enqueue_message_job

        row = get_section_row("bulletins", self.edit_id)
        if row is None:
            self.flash.error(t("Bulletin not found."))
            return
        success, message = enqueue_message_job(row, get_station_settings(), trigger="manual", force_send=True)
        if success:
            self.flash.success(t(message or "Bulletin queued."))
        else:
            self.flash.error(t(message or "Failed to send bulletin."))
        self.refresh()

    def delete(self) -> None:
        if self.edit_id is None:
            return
        if not confirm(self, t("Are you sure?"), t("Delete this bulletin?"), danger=True, confirm_text=t("Delete")):
            return
        from app.services.content import delete_section_row

        delete_section_row("bulletins", self.edit_id)
        self.new()
        self.flash.success(t("Bulletin deleted."))
        self.refresh()
