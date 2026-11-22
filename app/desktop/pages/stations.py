"""Stations page (web: templates/stations.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QToolButton

from app.desktop.i18n import t
from app.desktop.icons import icon, symbol_pixmap
from app.desktop.map_layers import parse_float, symbol_overlay
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.common import Card, Page, label, line_edit
from app.desktop.widgets.table import Column, DataTable

REFRESH_OPTIONS = ((5000, "5s"), (10000, "10s"), (30000, "30s"), (60000, "1min"), (300000, "5min"))
FILTERS = (
    ("all", "All stations", "account-group-outline"),
    ("direct", "Directly heard", "antenna"),
    ("mobile", "Mobile stations", "car-outline"),
    ("stationary", "Fixed stations", "home-outline"),
    ("object", "Objects", "crosshairs"),
    ("weather", "Weather stations", "weather-partly-cloudy"),
)


def entity_class(station: dict[str, Any]) -> str:
    value = str(station.get("entity_class") or "").strip().lower()
    return value if value in {"mobile", "object"} else "stationary"


def is_weather_station(station: dict[str, Any]) -> bool:
    return str(station.get("frame_type") or "").strip().upper() == "W" or str(station.get("symbol") or "").strip().endswith("_")


def matches_filter(station: dict[str, Any], name: str) -> bool:
    if name == "all":
        return True
    if name == "direct":
        return station.get("direct_heard") is True
    if name == "stationary":
        return entity_class(station) == "stationary" and not is_weather_station(station)
    if name == "weather":
        return is_weather_station(station)
    return entity_class(station) == name


def filter_counts(stations: list[dict[str, Any]]) -> dict[str, int]:
    return {name: sum(1 for station in stations if matches_filter(station, name)) for name, _caption, _icon in FILTERS}


def station_callsign(station: dict[str, Any]) -> str:
    return str(station.get("display_callsign") or station.get("callsign") or "")


def data_text(station: dict[str, Any]) -> str:
    items = station.get("data") or []
    return " · ".join(str(item.get("value")) for item in items if isinstance(item, dict) and item.get("value"))


def data_tooltip(station: dict[str, Any]) -> str:
    items = station.get("data") or []
    return "\n".join(f"{item.get('label')}: {item.get('value')}" for item in items if isinstance(item, dict) and item.get("value"))


class StationsPage(Page):
    page_key = "stations"

    def build(self) -> None:
        self.active_filter = "all"
        self.stations: list[dict[str, Any]] = []
        summary = Card()
        header = QHBoxLayout()
        header.addWidget(label(t("Stations"), "h2"))
        # Callsign search (the user's choice): Enter keeps the stations whose callsign contains the text,
        # together with the type filter; an empty field (Enter or the clear button) shows them all again.
        self.search = line_edit("", placeholder=t("Search callsign"))
        self.search.addAction(icon("magnify", None, 18), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.returnPressed.connect(self._search)
        self.search.textChanged.connect(lambda value: self._search() if not value else None)
        header.addWidget(self.search)
        header.addStretch(1)
        self.filter_buttons: dict[str, QToolButton] = {}
        for name, caption, icon_name in FILTERS:
            filter_button = QToolButton()
            filter_button.setProperty("variant", "secondary")
            filter_button.setCheckable(True)
            filter_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            filter_button.setIcon(icon(icon_name, None, 18))
            filter_button.setIconSize(QSize(18, 18))
            filter_button.setText("0")
            filter_button.setToolTip(t(caption))
            filter_button.setMinimumWidth(64)
            filter_button.clicked.connect(lambda _checked=False, value=name: self._set_filter(value))
            self.filter_buttons[name] = filter_button
            header.addWidget(filter_button)
        header.addSpacing(12)
        header.addWidget(label(t("Refresh rate"), "muted"))
        self.refresh_combo = QComboBox()
        for value, caption in REFRESH_OPTIONS:
            self.refresh_combo.addItem(caption, value)
        stored = int(self.context.ui_value("stations/refresh_ms", 10000) or 10000)
        index = self.refresh_combo.findData(stored)
        self.refresh_combo.setCurrentIndex(index if index >= 0 else 1)
        self.refresh_combo.currentIndexChanged.connect(self._refresh_rate_changed)
        header.addWidget(self.refresh_combo)
        summary.add(header)
        self.body.addWidget(summary)

        table_card = Card()
        columns = [
            Column("display_callsign", t("Callsign"), width=150,
                   display=lambda row: row.get("display_callsign") or row.get("callsign") or "",
                   secondary=lambda row: str(row.get("aprs_device_short") or ""),
                   sort_value=lambda row: str(row.get("display_callsign") or row.get("callsign") or "").casefold(),
                   bold=lambda row: True,
                   tooltip=lambda row: f"{t('APRS client')}: {row.get('aprs_device_short')}" if row.get("aprs_device_short") else ""),
            Column("frame_type", t("Type"), width=60, display=lambda row: row.get("frame_type") or "-",
                   tooltip=lambda row: str(row.get("frame_type_label") or row.get("frame_type") or "")),
            Column("last_heard_at", t("Last activity"), width=190,
                   display=lambda row: row.get("last_heard_date") or "",
                   secondary=lambda row: str(row.get("last_heard_relative") or ""),
                   sort_value=lambda row: str(row.get("last_heard_at") or "")),
            Column("symbol", t("APRS Icon"), width=96, display=lambda row: "" if row.get("symbol") else "-",
                   icon=lambda row: symbol_pixmap(row.get("symbol_icon"), symbol_overlay(row.get("symbol_table")), 22) if row.get("symbol") else None,
                   tooltip=lambda row: str(row.get("symbol") or "")),
            Column("latitude", t("Lat."), width=90, display=lambda row: row.get("latitude") or "-",
                   sort_value=lambda row: parse_float(row.get("latitude"))),
            Column("longitude", t("Lon."), width=90, display=lambda row: row.get("longitude") or "-",
                   sort_value=lambda row: parse_float(row.get("longitude"))),
            Column("distance_km", t("Distance"), width=90,
                   display=lambda row: f"{row['distance_km']} km" if row.get("distance_km") is not None else "-",
                   sort_value=lambda row: parse_float(row.get("distance_km")),
                   align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            Column("data", t("Data"), width=220, display=lambda row: data_text(row) or "-", tooltip=data_tooltip),
            Column("comment", t("Comment"), stretch=True, display=lambda row: row.get("comment") or "-",
                   tooltip=lambda row: str(row.get("comment") or "")),
        ]
        self.table = DataTable(columns, row_height=44)
        self.table.set_row_key(lambda row: row.get("display_callsign") or row.get("callsign"))
        self.table.row_activated.connect(self._open_station)
        self.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        table_card.add(self.table, 1)
        self.empty_label = label(t("No decoded APRS stations available yet."), "muted")
        table_card.add(self.empty_label)
        self.table.setMinimumHeight(520)
        self.body.addWidget(table_card, 1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = int(self.refresh_combo.currentData() or 10000)
        self._set_filter("all")

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.content import get_station_settings, projected_station_list

        with connection_scope():
            station_settings = get_station_settings(include_tx_runtime=False)
            state = projected_station_list(
                unit_system=station_settings.get("default_units", "metric"),
                station_settings=station_settings,
            )
        return state

    def _apply(self, state: dict[str, Any]) -> None:
        self.stations = list(state.get("stations") or [])
        counts = filter_counts(self.stations)
        for name, filter_button in self.filter_buttons.items():
            filter_button.setText(str(counts.get(name, 0)))
        self.table.set_rows(self.stations)
        self.table.set_filter(lambda row: matches_filter(row, self.active_filter))
        self.empty_label.setVisible(not self.stations)

    def _set_filter(self, name: str) -> None:
        self.active_filter = name
        for key, filter_button in self.filter_buttons.items():
            filter_button.setChecked(key == name)
        self.table.set_filter(lambda row: matches_filter(row, self.active_filter))

    def _search(self) -> None:
        self.table.set_text_filter(self.search.text(), station_callsign)

    def _refresh_rate_changed(self) -> None:
        value = int(self.refresh_combo.currentData() or 10000)
        self.context.set_ui_value("stations/refresh_ms", value)
        self.auto_refresh_ms = value
        if self._visible_on_screen:
            self._timer.start(value)

    def _open_station(self, row: dict[str, Any]) -> None:
        callsign = row.get("display_callsign") or row.get("callsign")
        if callsign:
            self.navigate("station-detail", str(callsign))
