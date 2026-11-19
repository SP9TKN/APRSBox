"""Frame details page (web: templates/traffic_frame_detail.html)."""

from __future__ import annotations

from typing import Any


from app.desktop.i18n import t
from app.desktop.routes import route_for_href
from app.desktop.tasks import run_async
from app.desktop.widgets.common import Card, Page, button, clear_layout, hbox, key_value_grid, label


class FrameDetailPage(Page):
    page_key = "frame-detail"

    def build(self) -> None:
        self.frame_id: int | None = None
        self.back = button(t("Back to Traffic Monitor"), variant="secondary", icon_name="arrow-left",
                           on_click=lambda: self.navigate("traffic"))
        self.body.addLayout(hbox(self.back, 1))
        self.card = Card(t("Frame details"))
        self.body.addWidget(self.card)
        self.body.addStretch(1)

    def open_argument(self, argument: Any) -> None:
        try:
            self.frame_id = int(argument)
        except (TypeError, ValueError):
            self.frame_id = None
        self.current_argument = self.frame_id

    def refresh(self) -> None:
        if self.frame_id is None:
            return
        frame_id = self.frame_id

        def load() -> dict[str, Any] | None:
            from app.services.alerts import get_traffic_frame

            return get_traffic_frame(frame_id)

        run_async(load, self._render, owner=self)

    def _render(self, frame: dict[str, Any] | None) -> None:
        clear_layout(self.card.body)
        if frame is None:
            self.card.set_title(t("Frame details"))
            self.card.add(label(t("Frame not found"), "error"))
            return
        self.card.set_title(f"{t('Frame details')} #{frame.get('id')}", str(frame.get("created_label") or ""))
        if frame.get("emergency"):
            if frame.get("alert_id"):
                link = button(f"{t('Related alert')}: #{frame.get('alert_id')} {frame.get('alert_source_callsign') or ''}",
                              variant="link", on_click=lambda: self.navigate("alert-detail", int(frame["alert_id"])))
                self.card.add(link)
            else:
                self.card.add(label(t("The related alert record was deleted."), "warning"))
        related = frame.get("related_entity") or {}
        source_widget: Any = frame.get("source_callsign") or "-"
        route = route_for_href(related.get("detail_href")) if isinstance(related, dict) else None
        if route is not None:
            source_widget = button(str(related.get("label") or frame.get("source_callsign") or "-"), variant="link",
                                   on_click=lambda: self.navigate(*route))
        raw = label(str(frame.get("line") or ""), "code", wrap=True, selectable=True)
        rows = [
            (t("Received"), frame.get("created_label")),
            (t("Source interface"), frame.get("source") or "-"),
            (t("Direction"), frame.get("direction") or "-"),
            (t("Band"), frame.get("band") or "-"),
            (t("Format"), frame.get("format")),
            (t("Source"), source_widget),
            (t("Path"), frame.get("path") or "-"),
            (t("Position"), frame.get("position_label") or "-"),
            (t("Port"), frame.get("port") or "-"),
            (t("Command"), frame.get("command") or "-"),
            (t("Raw frame"), raw),
        ]
        if frame.get("hex"):
            rows.append(("HEX", label(str(frame.get("hex")), "code", wrap=True, selectable=True)))
        self.card.add(key_value_grid(rows))
