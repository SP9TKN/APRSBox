"""Alert detail page (web: templates/alert_detail.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from app.desktop.help import show_help
from app.desktop.i18n import t
from app.desktop.map_layers import alert_area_shapes, symbol_overlay
from app.desktop.pages.alerts import alert_category_color, mute_combo
from app.desktop.routes import route_for_href
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.common import (
    Badge,
    Card,
    Page,
    button,
    clear_layout,
    combo_value,
    confirm,
    icon_button,
    key_value_grid,
    label,
)
from app.desktop.widgets.map_view import (
    MapMarker,
    MapView,
    geojson_polygons,
    map_mask,
    stored_mask_percent,
)
from app.desktop.widgets.table import Column, DataTable


class AlertDetailPage(Page):
    page_key = "alert-detail"

    def build(self) -> None:
        self.alert_id: int | None = None
        self.alert: dict[str, Any] | None = None
        self._centered_for: Any = None
        manage = self.can_edit

        self.header_card = Card(t("Alert details"))
        self.header_card.add_action(button(t("Back"), variant="secondary", icon_name="arrow-left", on_click=lambda: self.navigate("alerts")))
        self.station_button = button(t("Station"), variant="secondary", icon_name="account-outline", on_click=self._open_station)
        self.header_card.add_action(self.station_button)
        if manage:
            self.mute_combo = mute_combo()
            self.header_card.add_action(self.mute_combo)
            self.header_card.add_action(button(t("Mute"), variant="secondary", icon_name="volume-off", on_click=self._mute))
            self.unmute_button = button(t("Cancel mute"), variant="secondary", icon_name="volume-high", on_click=self._unmute)
            self.header_card.add_action(self.unmute_button)
            self.header_card.add_action(button(t("Delete"), variant="danger", icon_name="delete-outline", on_click=self._delete))
        self.header_card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/alerts")))
        self.header_card.add(self.flash)
        self.body.addWidget(self.header_card)

        hero = QHBoxLayout()
        hero.setSpacing(14)
        self.details_card = Card(t("Alert details"))
        self.details_host = QVBoxLayout()
        self.details_card.add(self.details_host)
        self.details_card.body.addStretch(1)
        hero.addWidget(self.details_card, 1)
        self.map_card = Card(t("Alert areas"))
        self.map = MapView(self, show_zoom_control=True)
        self.map.setMinimumHeight(380)
        self.map_card.add(self.map, 1)
        self.map_placeholder = label("", "muted", wrap=True)
        self.map_card.add(self.map_placeholder)
        hero.addWidget(self.map_card, 1)
        self.body.addLayout(hero)

        self.parts_card = Card(t("Alert parts"))
        self.parts_table = DataTable(
            [
                Column("part", t("Part"), width=70,
                       display=lambda part: f"{part.get('part_number') or '?'} / {part.get('parts_total') or '?'}"),
                Column("aprs_message_id", t("APRS message ID"), width=120, mono=True, display=lambda part: part.get("aprs_message_id") or "-"),
                Column("area_codes", t("Area codes"), width=160, mono=True,
                       display=lambda part: ", ".join(part.get("area_codes") or []) or "-"),
                Column("first_received_label", t("First received"), width=150),
                Column("last_received_label", t("Last received"), width=150),
                Column("received_count", t("Received count"), width=90),
                Column("raw_message", t("Raw message"), stretch=True, mono=True, tooltip=lambda part: str(part.get("raw_message") or "")),
                Column("initial_frame_id", t("Original frame"), width=110,
                       display=lambda part: f"#{part.get('initial_frame_id')}" if part.get("initial_frame_href") else "-",
                       tooltip=lambda part: "" if part.get("initial_frame_href") else t("Frame is no longer available in Traffic Monitor.")),
            ],
            sortable=False,
        )
        self.parts_table.row_activated.connect(self._open_part_frame)
        self.parts_card.add(self.parts_table)
        self.body.addWidget(self.parts_card)

        history_card = Card(t("Related frame history"))
        self.frames_table = DataTable(
            [
                Column("created_label", t("Received"), width=160),
                Column("line", t("Raw frame"), stretch=True, mono=True, tooltip=lambda frame: str(frame.get("line") or "")),
                Column("source", t("Source interface"), width=140, display=lambda frame: frame.get("source") or "-"),
                Column("path", t("Path"), width=170, display=lambda frame: frame.get("path") or "-"),
                Column("position_label", t("Position"), width=150, display=lambda frame: frame.get("position_label") or "-"),
            ],
            sortable=False,
        )
        self.frames_table.set_empty_text(t("No related frames are currently available in Traffic Monitor."))
        self.frames_table.setMinimumHeight(200)
        self.frames_table.row_activated.connect(self._open_frame)
        history_card.add(self.frames_table)
        history_card.add(label(t("Double-click a frame to open its details."), "small"))
        self.body.addWidget(history_card)
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000

    # ------------------------------------------------------------- loading
    def open_argument(self, argument: Any) -> None:
        try:
            alert_id = int(argument)
        except (TypeError, ValueError):
            return
        if alert_id != self.alert_id:
            self.alert_id = alert_id
            self._centered_for = None
            self.alert = None
            self.flash.hide()

    def refresh(self) -> None:
        if self.alert_id is not None:
            self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.alerts import get_alert
        from app.services.map_service import get_alert_detail_map_config

        alert_id = self.alert_id
        with connection_scope():
            alert = get_alert(int(alert_id)) if alert_id is not None else None
            config = get_alert_detail_map_config(alert) if alert is not None else {}
            if alert is not None and config.get("map_mode") == "station":
                from app.services.map_service import get_station_detail_track_payload

                related = alert.get("related_entity") if isinstance(alert.get("related_entity"), dict) else {}
                display = str(related.get("label") or alert.get("source_callsign") or "")
                config["track"] = get_station_detail_track_payload(display)
        return {"alert_id": alert_id, "alert": alert, "map": config}

    def _apply(self, data: dict[str, Any]) -> None:
        if data.get("alert_id") != self.alert_id:
            return
        alert = data.get("alert")
        if alert is None:
            self.alert = None
            self.header_card.set_title(f"{t('Alert details')} #{self.alert_id}")
            self.flash.error(t("Alert not found"))
            return
        self.alert = alert
        self._render(alert, data.get("map") or {})

    # ----------------------------------------------------------- rendering
    def _render(self, alert: dict[str, Any], map_config: dict[str, Any]) -> None:
        self.header_card.set_title(
            f"{t('Alert details')} #{alert.get('id')}",
            f"{alert.get('source_callsign') or ''} · {alert.get('alert_type') or ''}",
        )
        related = alert.get("related_entity") if isinstance(alert.get("related_entity"), dict) else {}
        self.station_button.setEnabled(route_for_href(related.get("detail_href")) is not None)
        self.station_button.setText(str(related.get("label") or alert.get("source_callsign") or t("Station")))
        if self.can_edit:
            self.unmute_button.setVisible(bool(alert.get("muted")))
        clear_layout(self.details_host)
        type_badge = Badge(str(alert.get("alert_type") or ""), "error")
        rows: list[tuple[str, Any]] = [
            (t("Source"), alert.get("source_callsign")),
            (t("Type"), type_badge),
        ]
        interpretation = alert.get("interpretation") or {}
        if alert.get("destination_group"):
            severity = t(interpretation.get("severity_level_label") or "")
            if interpretation.get("severity_color_label"):
                severity += f" — {t(interpretation.get('severity_color_label'))}"
            severity_label = label(severity or "-", wrap=True, selectable=True)
            severity_label.setStyleSheet(f"color: {alert_category_color(alert).name()}; font-weight: 700;")
            complete = alert.get("completion_status") == "complete"
            rows += [
                (t("Destination group"), alert.get("destination_group")),
                (t("Logical alert ID"), alert.get("logical_alert_id") or "-"),
                (t("Warning format"), interpretation.get("format_label") or "-"),
                (t("Event description"), t(interpretation.get("event_label") or "") or "-"),
                (t("Event code"), alert.get("event_code") or "-"),
                (t("Threat level"), severity_label),
                (t("Comment"), alert.get("protocol_comment") or "-"),
                (t("Received parts"), f"{alert.get('received_parts') or 0} / {alert.get('parts_total') or '?'}"),
                (t("Completion status"), Badge(t("Complete alert") if complete else t("Incomplete"), "ok" if complete else "warn")),
                (t("Area count"), alert.get("area_count") or 0),
                (t("Area codes"), ", ".join(alert.get("area_codes") or []) or "-"),
                (t("Expires at"), alert.get("expires_at_label") or "-"),
            ]
        else:
            rows.append((t("Comment"), alert.get("message") or "-"))
        latitude, longitude = alert.get("latitude"), alert.get("longitude")
        if alert.get("muted_indefinitely"):
            mute_text = t("Alert muted indefinitely")
        elif alert.get("muted"):
            mute_text = f"{t('Muted until')} {alert.get('muted_until_label') or ''}"
        else:
            mute_text = t("Not muted")
        rows += [
            (t("First received"), alert.get("first_seen_label")),
            (t("Last received"), alert.get("last_seen_label")),
            (t("Frame count"), alert.get("frame_count")),
            (t("Last position"), f"{latitude}, {longitude}" if latitude is not None and longitude is not None else t("No position available")),
            (t("Mute"), mute_text),
            (t("Related station or object"), related.get("label") or "-"),
            (t("Last frame"), f"#{alert.get('last_frame_id')}" if alert.get("last_frame_href") else t("Frame is no longer available in Traffic Monitor.")),
        ]
        self.details_host.addWidget(key_value_grid(rows))
        self._render_map(alert, map_config)
        parts = list(alert.get("parts") or [])
        self.parts_card.setVisible(bool(parts))
        self.parts_table.set_rows(parts)
        self.parts_table.fit_height(max_rows=8)
        self.frames_table.set_rows(list(alert.get("frames") or []))

    def _render_map(self, alert: dict[str, Any], config: dict[str, Any]) -> None:
        self.map.set_mask(*map_mask(stored_mask_percent(self.context, self.map.tiles.source.source_id,
                                                        dark=self._is_dark())))
        if config.get("map_mode") == "station":
            self.map_card.set_title(t("Station Map"))
            latitude, longitude = config.get("latitude"), config.get("longitude")
            has_position = latitude is not None and longitude is not None
            self.map.setVisible(has_position)
            self.map_placeholder.setVisible(not has_position)
            self.map_placeholder.setText(f"{t('No position available')}\n{t('No position available for this station.')}")
            self.map.set_layer("areas", [])
            if not has_position:
                return
            from app.desktop.map_layers import track_shapes

            display = str(config.get("display_callsign") or alert.get("source_callsign") or "")
            self.map.set_markers([
                MapMarker(key=display, latitude=float(latitude), longitude=float(longitude), label=display,
                          icon_path=str(config.get("symbol_icon") or ""), overlay=symbol_overlay(config.get("symbol_table")))
            ])
            points = (config.get("track") or {}).get("points") or []
            self.map.set_layer("tracks", track_shapes([{"display_callsign": display, "points": points}]))
            if self._centered_for != ("station", alert.get("id")):
                self._centered_for = ("station", alert.get("id"))
                self.map.set_view(float(latitude), float(longitude), int(config.get("zoom") or 13))
            return
        self.map_card.set_title(t("Alert areas"))
        features = config.get("feature_collection") or {}
        has_areas = bool(config.get("has_area_definitions"))
        self.map.setVisible(has_areas)
        self.map_placeholder.setVisible(not has_areas)
        self.map_placeholder.setText(t("No area definitions"))
        self.map.set_markers([])
        self.map.set_layer("tracks", [])
        self.map.set_layer("areas", alert_area_shapes(features, fill_opacity=0.18, width=2.0))
        if has_areas and self._centered_for != ("areas", alert.get("id")):
            self._centered_for = ("areas", alert.get("id"))
            points = [point for rings, _properties in geojson_polygons(features) for ring in rings for point in ring]
            if points:
                self.map.fit_points(points, padding=28, max_zoom=12)

    @staticmethod
    def _is_dark() -> bool:
        from app.desktop.theme import current_colors

        return current_colors().is_dark

    # -------------------------------------------------------------- actions
    def _open_station(self) -> None:
        related = (self.alert or {}).get("related_entity") or {}
        route = route_for_href(related.get("detail_href") if isinstance(related, dict) else None)
        if route is not None:
            self.navigate(*route)

    def _open_frame(self, frame: dict[str, Any]) -> None:
        route = route_for_href(frame.get("detail_href"))
        if route is not None:
            self.navigate(*route)

    def _open_part_frame(self, part: dict[str, Any]) -> None:
        route = route_for_href(part.get("initial_frame_href"))
        if route is not None:
            self.navigate(*route)

    def _mute(self) -> None:
        if self.alert_id is None:
            return
        from app.services.alerts import mute_alert

        try:
            changed = mute_alert(self.alert_id, str(combo_value(self.mute_combo) or "1h"))
        except ValueError:
            self.flash.error(t("Unsupported mute duration."))
            return
        if not changed:
            self.flash.error(t("Alert not found"))
            return
        self.flash.success(t("Alert muted."))
        self.context.notify_data_changed("alerts")
        self.refresh()

    def _unmute(self) -> None:
        if self.alert_id is None:
            return
        from app.services.alerts import unmute_alert

        unmute_alert(self.alert_id)
        self.flash.success(t("Alert unmuted."))
        self.context.notify_data_changed("alerts")
        self.refresh()

    def _delete(self) -> None:
        if self.alert_id is None:
            return
        if not confirm(self, t("Delete"), t("Delete this alert? Original frames remain in Traffic Monitor."), danger=True,
                       confirm_text=t("Delete")):
            return
        from app.services.alerts import delete_alert

        delete_alert(self.alert_id)
        self.context.notify_data_changed("alerts")
        self.alert_id = None
        self.navigate("alerts")
