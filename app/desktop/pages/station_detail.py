"""Station detail page (web: templates/station_detail.html + station-detail-map.js)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.desktop.i18n import t, tf
from app.desktop.map_layers import (
    TrackReceptionLines,
    parse_float,
    path_shapes,
    station_positions,
    symbol_overlay,
    track_shapes,
)
from app.desktop.rf_paths import build_segments, load_recent_routes, own_station_identity
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.common import (
    Card,
    FlowLayout,
    Page,
    button,
    clear_layout,
    hbox,
    icon_button,
    key_value_grid,
    label,
)
from app.desktop.widgets.map_view import MapMarker, MapView, map_mask, stored_mask_percent
from app.desktop.widgets.table import Column, DataTable
from app.desktop.widgets.traffic_analyzer import TrafficAnalyzerCard
from app.desktop.widgets.weather_charts import WeatherPanel


def station_detail_payload(callsign: str) -> dict[str, Any] | None:
    """Same data as pages._station_detail_context (+ RF path routes for the map)."""
    from app.db import connection_scope, get_traffic_retention_minutes
    from app.services.content import get_related_ssids, get_station_detail, get_station_packets_in_retention, get_station_settings
    from app.services.map_service import get_station_detail_track_payload
    from app.services.map_station_state import read_map_station_state

    with connection_scope():
        station_settings = get_station_settings(include_tx_runtime=False)
        state = read_map_station_state(station_settings=station_settings)
        snapshots = state["snapshots"]
        detail = get_station_detail(callsign, unit_system=station_settings.get("default_units", "metric"), snapshots=snapshots)
        if detail is None:
            return None
        related = get_related_ssids(detail["base_callsign"], snapshots=snapshots)
        for item in related:
            item["is_current"] = item["display_callsign"].casefold() == detail["display_callsign"].casefold()
        track = get_station_detail_track_payload(detail["display_callsign"])
        # Every frame of the station the database keeps (the user: the whole retention, not the last 10).
        packets = get_station_packets_in_retention(detail["display_callsign"], snapshot=detail)
        retention_minutes = int(get_traffic_retention_minutes())
        from app.services.weather_history import get_weather_history

        weather = get_weather_history(detail["display_callsign"])  # 7 days for the charts
        direct = any(
            bool(snapshot.get("direct_heard"))
            for snapshot in snapshots
            if str(snapshot.get("display_callsign") or "").casefold() == detail["display_callsign"].casefold()
        )
        own = own_station_identity(station_settings)
        routes = [route for route in load_recent_routes(24 * 3600, own) if route.station.casefold() == detail["display_callsign"].casefold()]
        positions = station_positions(snapshots)
        own_lat = parse_float(station_settings.get("latitude"))
        own_lon = parse_float(station_settings.get("longitude"))
        if own and own_lat is not None and own_lon is not None:
            positions[own] = (own_lat, own_lon)
    return {
        "station": detail,
        "related": related,
        "track": track,
        "packets": packets,
        "retention_minutes": retention_minutes,
        "direct_heard": direct,
        "routes": routes,
        "positions": positions,
        "own_key": own,
        "weather": weather,
    }


class StationDetailPage(Page):
    page_key = "station-detail"

    def build(self) -> None:
        self.callsign = ""
        self.current_argument = None
        back = button(t("Stations"), variant="secondary", icon_name="arrow-left", on_click=lambda: self.navigate("stations"))
        # The user's choice: right of the back arrow; the analysis appears at the bottom of the card.
        self.analyzer_button = button(t("Traffic analyzer"), variant="secondary", icon_name="chart-box-outline",
                                      on_click=self._open_analyzer)
        self.body.addLayout(hbox(back, self.analyzer_button, 1))
        self.header_card = Card()
        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.title_label = label("", "page-title")
        self.meta_label = label("", "muted", wrap=True, selectable=True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.meta_label)
        header.addLayout(titles, 1)
        self.message_button = icon_button("forum", t("Open in Messages"), self._open_messages)
        self.message_button.setVisible(self.can_edit)
        header.addWidget(self.message_button, 0, Qt.AlignmentFlag.AlignTop)
        self.header_card.add(header)
        self.header_card.add(self.flash)
        self.body.addWidget(self.header_card)

        hero = QHBoxLayout()
        hero.setSpacing(14)
        self.details_card = Card(t("Station Details"))
        self.fields_host = QVBoxLayout()
        self.details_card.add(self.fields_host)
        self.data_host = QWidget()
        self.data_layout = FlowLayout(self.data_host, spacing=6)
        self.details_card.add(self.data_host)
        self.details_card.body.addStretch(1)
        hero.addWidget(self.details_card, 1)

        self.map_card = Card(t("Station Map"))
        self.map = MapView(self, show_zoom_control=True)
        self.map.setMinimumHeight(340)
        self.map.marker_clicked.connect(lambda payload: None)
        self._map_positions: dict[str, tuple[float, float]] = {}
        self._own_key = ""
        self.reception_lines = TrackReceptionLines(self.map, positions=lambda: self._map_positions,
                                                   own_key=lambda: self._own_key)
        self.map_card.add(self.map, 1)
        self.no_position = QFrame()
        no_position_layout = QVBoxLayout(self.no_position)
        no_position_layout.addWidget(label(t("No position available"), "h3"))
        no_position_layout.addWidget(label(t("No position available for this station."), "muted"))
        self.map_card.add(self.no_position)
        self.device_title = label(t("APRS device identification"), "h2")
        self.map_card.add(self.device_title)
        self.device_host = QVBoxLayout()
        self.map_card.add(self.device_host)
        hero.addWidget(self.map_card, 1)
        self.body.addLayout(hero)

        # Weather charts (the user's choice): only for a station that sends weather reports.
        self.weather_card = Card(t("Weather"))
        self.weather_panel = WeatherPanel()
        self.weather_card.add(self.weather_panel)
        self.weather_card.setVisible(False)
        self.body.addWidget(self.weather_card)

        self.packets_card = Card(t("Recent Packets / Frames"))
        self.packets_note = label("", "muted")
        self.packets_card.add(self.packets_note)
        self.packets_table = DataTable(
            [
                Column("timestamp", t("Timestamp"), width=200,
                       display=lambda row: row.get("timestamp_label") or "",
                       secondary=lambda row: str(row.get("timestamp_relative") or ""),
                       sort_value=lambda row: str(row.get("timestamp") or "")),
                Column("destination", t("Destination"), width=110),
                Column("raw_packet", t("Raw packet"), stretch=True, mono=True,
                       tooltip=lambda row: str(row.get("raw_packet") or "")),
            ],
            row_height=42,
        )
        self.packets_table.setMinimumHeight(260)
        self.packets_card.add(self.packets_table)
        self.no_packets = label(t("No stored packet history is available for this station yet."), "muted")
        self.packets_card.add(self.no_packets)
        self.body.addWidget(self.packets_card)

        self.ssids_card = Card(t("Other Known SSIDs"))
        self.ssids_host = QWidget()
        self.ssids_layout = FlowLayout(self.ssids_host, spacing=6)
        self.ssids_card.add(self.ssids_host)
        self.body.addWidget(self.ssids_card)
        self.analyzer_card = TrafficAnalyzerCard(on_shown=self._scroll_to_analyzer, context=self.context,
                                                 on_open_station=lambda callsign: self.navigate("station-detail", callsign))
        self.body.addWidget(self.analyzer_card)
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 30000
        self._centered_for = ""

    def open_argument(self, argument: Any) -> None:
        if argument:
            if str(argument) != self.callsign and hasattr(self, "analyzer_card"):
                self.analyzer_card.reset()  # another station: the old analysis goes
            self.callsign = str(argument)
            self.current_argument = self.callsign

    def refresh(self) -> None:
        if self.callsign:
            self.loader.request()

    def _load(self) -> dict[str, Any] | None:
        return station_detail_payload(self.callsign)

    def _apply(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            self.title_label.setText(self.callsign)
            self.meta_label.setText(t("Station not found"))
            return
        station = payload["station"]
        self.title_label.setText(str(station.get("display_callsign") or ""))
        meta = [f"{t(station.get('activity_label') or 'Last heard')} {station.get('last_heard_date') or ''}"]
        if station.get("last_heard_relative"):
            meta[0] += f" ({station.get('last_heard_relative')})"
        if station.get("source"):
            meta.append(f"{t('Source')} {station.get('source')}")
        if station.get("path"):
            meta.append(f"{t('Path')} {station.get('path')}")
        if station.get("last_heard_rf_at"):
            text = f"{t('Last heard via RF')} {station.get('last_heard_rf_date') or ''}"
            if station.get("last_heard_rf_relative"):
                text += f" ({station.get('last_heard_rf_relative')})"
            meta.append(text)
        if station.get("last_seen_aprsis_at"):
            text = f"{t('Last seen via APRS-IS')} {station.get('last_seen_aprsis_date') or ''}"
            if station.get("last_seen_aprsis_relative"):
                text += f" ({station.get('last_seen_aprsis_relative')})"
            meta.append(text)
        meta.append(t("Heard directly: yes") if payload.get("direct_heard") else t("Heard directly: no"))
        self.meta_label.setText(" • ".join(meta))
        self._render_fields(station)
        self._render_map(payload)
        self._render_device(station.get("aprs_device"))
        weather = list(payload.get("weather") or [])
        self.weather_card.setVisible(bool(weather))
        self.weather_panel.set_observations(weather)
        packets = payload.get("packets") or []
        self.packets_table.set_rows(packets)
        self.packets_table.setVisible(bool(packets))
        self.no_packets.setVisible(not packets)
        from app.desktop.pages.traffic import format_minutes

        self.packets_note.setText(tf("Frames from the last {retention} (traffic history retention): {count}",
                                     retention=format_minutes(int(payload.get("retention_minutes") or 0)),
                                     count=len(packets)))
        self.packets_note.setVisible(bool(packets))
        self._render_ssids(payload.get("related") or [], station)

    def _render_fields(self, station: dict[str, Any]) -> None:
        clear_layout(self.fields_host)
        rows: list[tuple[str, Any]] = []
        for field in station.get("fields") or []:
            caption = str(field.get("label") or "")
            value = field.get("value")
            if caption == t("Latest raw packet"):
                rows.append((caption, label(str(value or ""), "code", wrap=True, selectable=True)))
            elif field.get("html") is not None:
                widget = label(str(field.get("html") or ""), None, wrap=True, rich=True)
                widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                rows.append((caption, widget))
            else:
                rows.append((caption, value if value not in (None, "") else "-"))
        self.fields_host.addWidget(key_value_grid(rows))
        self.data_layout.clear()
        data = station.get("data") or []
        self.data_host.setVisible(bool(data))
        for item in data:
            chip = label(f"{item.get('label')}: {item.get('value')}", "badge")
            chip.setProperty("badge", True)
            chip.setToolTip(f"{item.get('label')}: {item.get('value')}")
            self.data_layout.addWidget(chip)
            chip.show()

    def _render_map(self, payload: dict[str, Any]) -> None:
        station = payload["station"]
        latitude = station.get("latitude_float")
        longitude = station.get("longitude_float")
        has_position = latitude is not None and longitude is not None
        self.map.setVisible(has_position)
        self.no_position.setVisible(not has_position)
        if not has_position:
            return
        self.map.reload_tile_source()
        try:
            from app.services.content import get_aprs_symbol_set

            self.map.set_modern_symbols(get_aprs_symbol_set() == "modern")
        except Exception:
            pass
        self.map.set_mask(*map_mask(stored_mask_percent(self.context, self.map.tiles.source.source_id,
                                                        dark=not self._is_light())))
        display = str(station.get("display_callsign") or "")
        self.map.set_markers(
            [
                MapMarker(
                    key=display,
                    latitude=float(latitude),
                    longitude=float(longitude),
                    label=display,
                    icon_path=str(station.get("symbol_icon") or ""),
                    overlay=symbol_overlay(station.get("symbol_table")),
                    payload=station,  # a right click draws the way of its latest frame
                )
            ]
        )
        track = payload.get("track") or {}
        tracks = [{"display_callsign": display, "points": track.get("points") or []}]
        self._own_key = str(payload.get("own_key") or "")
        self._map_positions = dict(payload.get("positions") or {})
        self.map.set_layer("tracks", track_shapes(tracks, own_key=self._own_key, pick_hint=True))
        segments = build_segments(payload.get("routes") or [], payload.get("positions") or {})
        self.map.set_layer("paths", path_shapes(segments))
        if self._centered_for != display:
            self._centered_for = display
            self.reception_lines.clear()  # another station: its own dots
            self.map.set_view(float(latitude), float(longitude), 14)
        else:
            self.reception_lines.refresh()

    def _is_light(self) -> bool:
        from app.desktop.theme import current_colors

        return not current_colors().is_dark

    def _render_device(self, device: dict[str, Any] | None) -> None:
        clear_layout(self.device_host)
        self.device_title.setVisible(bool(device))
        if not device:
            return
        summary = str(device.get("identified_as") or "")
        if device.get("vendor") and device.get("vendor") != device.get("identified_as"):
            summary += f"  • {device.get('vendor')}"
        self.device_host.addWidget(label(summary, "h3"))
        rows: list[tuple[str, Any]] = []
        if device.get("identifier_kind") == "tocall" and device.get("actual_identifier"):
            rows.append((t("TOCALL"), label(str(device.get("actual_identifier")), "code")))
        elif device.get("actual_identifier"):
            rows.append((t("Mic-E"), label(str(device.get("actual_identifier")), "code")))
        rows.append((t("Identified software/device"), device.get("identified_as")))
        for key, caption in (("vendor", "Vendor"), ("model", "Model"), ("class_label", "Class"),
                             ("class_description", "Class description"), ("os", "OS")):
            if device.get(key):
                rows.append((t(caption), device.get(key)))
        self.device_host.addWidget(key_value_grid(rows))
        features = device.get("features") or []
        if features:
            self.device_host.addWidget(label(t("Capabilities"), "muted"))
            host = QWidget()
            flow = FlowLayout(host, spacing=6)
            for feature in features:
                chip = QLabel(str(feature))
                chip.setProperty("badge", True)
                flow.addWidget(chip)
            self.device_host.addWidget(host)

    def _render_ssids(self, related: list[dict[str, Any]], station: dict[str, Any]) -> None:
        self.ssids_layout.clear()
        if len(related) <= 1:
            info = label(tf("No other known SSIDs are currently available for {callsign}.", callsign=station.get("base_callsign") or ""), "muted")
            self.ssids_layout.addWidget(info)
            info.show()
            return
        for item in related:
            text = str(item.get("display_callsign") or "")
            if item.get("is_current"):
                text += f"  ({t('current')})"
            chip = button(text, variant="secondary")
            chip.setCheckable(True)
            chip.setChecked(bool(item.get("is_current")))
            chip.setToolTip(str(item.get("last_heard_label") or ""))
            callsign = str(item.get("display_callsign") or "")
            chip.clicked.connect(lambda _checked=False, value=callsign: self.navigate("station-detail", value))
            self.ssids_layout.addWidget(chip)
            chip.show()

    def _open_analyzer(self) -> None:
        self.analyzer_card.start(self.callsign)
        self._scroll_to_analyzer()

    def _scroll_to_analyzer(self) -> None:
        scroll = getattr(self, "scroll", None)
        if scroll is not None:
            QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(self.analyzer_card, 0, 0))

    def _open_messages(self) -> None:
        if self.callsign:
            self.navigate("messages", {"callsign": self.title_label.text() or self.callsign})
