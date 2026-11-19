"""Packet Routing flows list (web: templates/digi_flows.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.common import Card, Page, button, confirm, icon_button, tone_color
from app.desktop.widgets.crud import RowActionBar
from app.desktop.widgets.table import Column, DataTable


class DigiFlowsPage(Page):
    page_key = "digi-flows"

    def build(self) -> None:
        self.flows: list[dict[str, Any]] = []
        card = Card(t("Configured Packet Routing Flows"))
        card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/packet_routing")))
        if self.can_edit:
            card.add_action(button(t("New routing flow"), icon_name="plus", on_click=lambda: self.navigate("digi-flow-edit", None)))
        card.add(self.flash)
        self.table = DataTable(
            [
                Column("order", t("Rule"), width=60, align=Qt.AlignmentFlag.AlignCenter),
                Column("name", t("Name"), width=200, bold=lambda flow: True),
                Column("description", t("Description"), stretch=True, display=lambda flow: flow.get("description") or "-",
                       tooltip=lambda flow: str(flow.get("description") or "")),
                Column("source_display", t("Source"), width=200, display=lambda flow: flow.get("source_display") or "-"),
                Column("target_display", t("Target"), width=200, display=lambda flow: flow.get("target_display") or "-"),
                Column("enabled", t("Status"), width=120,
                       display=lambda flow: t("Enabled") if flow.get("enabled") else t("Disabled"),
                       icon=lambda flow: icon_pixmap("check-circle" if flow.get("enabled") else "close-circle",
                                                     tone_color("ok" if flow.get("enabled") else "neutral"), 18),
                       foreground=lambda flow: tone_color("ok") if flow.get("enabled") else None),
            ],
            row_height=34,
            sortable=False,
        )
        self.table.set_empty_text(t("No Packet Routing flows configured yet."))
        self.table.setMinimumHeight(360)
        card.add(self.table, 1)
        self.actions = RowActionBar(self.table)
        if self.can_edit:
            self.table.row_activated.connect(self._edit)
            self.actions.add(t("Move up"), "arrow-up", lambda flow: self._move(flow, "up"),
                             enabled_for=lambda flow: int(flow.get("order") or 1) > 1)
            self.actions.add(t("Move down"), "arrow-down", lambda flow: self._move(flow, "down"),
                             enabled_for=lambda flow: int(flow.get("order") or 0) < len(self.flows))
            self.actions.add(t("Edit"), "pencil", self._edit)
            self.toggle_button = self.actions.add(t("Disable"), "pause-circle-outline", self._toggle)
            self.actions.add(t("Duplicate"), "content-copy", self._duplicate)
            self.actions.add(t("Delete"), "delete-outline", self._delete, variant="danger")
            self.table.selection_changed_rows.connect(lambda _rows: self._sync_toggle())
        card.add(self.actions)
        self.body.addWidget(card, 1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000

    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict) and argument.get("flash"):
            message = t(str(argument["flash"]))
            if argument.get("success", True) in (False, 0, "0"):
                self.flash.error(message)
            else:
                self.flash.success(message)

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> list[dict[str, Any]]:
        from app.services.digi_flows import list_digi_flows

        return list_digi_flows()

    def _apply(self, flows: list[dict[str, Any]]) -> None:
        self.flows = [{**flow, "order": index} for index, flow in enumerate(flows or [], start=1)]
        self.table.set_rows(self.flows)
        self.actions.sync()
        if self.can_edit:
            self._sync_toggle()

    def _sync_toggle(self) -> None:
        from app.desktop.icons import icon

        flow = self.table.current_row()
        enabled = bool(flow.get("enabled")) if flow else True
        self.toggle_button.setText(t("Disable") if enabled else t("Enable"))
        self.toggle_button.setIcon(icon("pause-circle-outline" if enabled else "play-circle-outline", None, 16))

    # ------------------------------------------------------------- actions
    def _edit(self, flow: dict[str, Any]) -> None:
        self.navigate("digi-flow-edit", int(flow["id"]))

    def _duplicate(self, flow: dict[str, Any]) -> None:
        self.navigate("digi-flow-edit", {"duplicate": int(flow["id"])})

    def _move(self, flow: dict[str, Any], direction: str) -> None:
        from app.services.digi_flows import safe_move_digi_flow

        error = safe_move_digi_flow(int(flow["id"]), direction)
        if error:
            self.flash.error(t(error))
            return
        self.flash.hide()
        self._reload_selecting(int(flow["id"]))

    def _toggle(self, flow: dict[str, Any]) -> None:
        from app.services.digi_flows import set_digi_flow_enabled

        try:
            set_digi_flow_enabled(int(flow["id"]), not bool(flow.get("enabled")))
        except ValueError as exc:
            self.flash.error(t(str(exc)))
            return
        self.flash.success(t("Packet Routing flow status updated."))
        self._reload_selecting(int(flow["id"]))

    def _delete(self, flow: dict[str, Any]) -> None:
        if not confirm(self, t("Delete"), tf("Delete Packet Routing flow {name}?", name=flow.get("name")), danger=True,
                       confirm_text=t("Delete")):
            return
        from app.services.digi_flows import delete_digi_flow

        delete_digi_flow(int(flow["id"]))
        self.flash.success(t("Packet Routing flow deleted."))
        self.refresh()

    def _reload_selecting(self, flow_id: int) -> None:
        from app.services.digi_flows import list_digi_flows

        self._apply(list_digi_flows())
        self.table.select_key(flow_id)
