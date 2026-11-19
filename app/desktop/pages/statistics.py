"""Statistics page (web: templates/statistics.html + static/js/statistics.js)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout

from app.desktop.i18n import t
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.charts import SLOT_ORDER, Bar, BarList, LineChart, Series
from app.desktop.widgets.common import Card, Page, button, combo, combo_value, label, set_combo_value

RANGES = [("1h", "1 hour"), ("24h", "1 day"), ("7d", "7 days"), ("30d", "30 days")]
RANGE_SETTING_KEY = "statistics/range"
ACTION_SLOTS = {"rx": "ink", "tx": "blue", "digipeated": "orange", "gated_to_aprsis": "aqua", "filtered_dropped": "yellow"}
HEARD_SLOTS = {"direct_heard": "blue", "all_heard": "ink"}
FRAME_TYPE_HUES = len(SLOT_ORDER)


def aggregation_text(bucket_minutes: Any) -> str:
    minutes = int(bucket_minutes or 5)
    if minutes >= 1440 and minutes % 1440 == 0:
        return f"{t('aggregation')} {minutes // 1440}d"
    return f"{t('aggregation')} {minutes}min"


def frame_type_series(series: list[dict[str, Any]]) -> list[Series]:
    """Top four frame types by volume get the hue slots; the rest fold into a neutral 'Other'."""
    totals = [(sum(float(value or 0) for value in item.get("data") or []), index, item) for index, item in enumerate(series)]
    ranked = [entry for entry in sorted(totals, key=lambda entry: (-entry[0], entry[1])) if entry[0] > 0]
    top = sorted(ranked[:FRAME_TYPE_HUES], key=lambda entry: entry[1])
    result = [
        Series(str(item.get("key")), t(item.get("label") or item.get("key")), [float(value or 0) for value in item.get("data") or []],
               slot=SLOT_ORDER[position], fill=False)
        for position, (_total, _index, item) in enumerate(top)
    ]
    rest = [entry[2] for entry in ranked[FRAME_TYPE_HUES:]]
    if rest:
        length = max(len(item.get("data") or []) for item in rest)
        folded = [sum(float((item.get("data") or [0] * length)[index] or 0) for item in rest) for index in range(length)]
        result.append(Series("other", t("Other"), folded, slot="ink"))
    return result


def slotted_series(series: list[dict[str, Any]], slots: dict[str, str]) -> list[Series]:
    return [
        Series(str(item.get("key")), t(item.get("label") or item.get("key")), [float(value or 0) for value in item.get("data") or []],
               slot=slots.get(str(item.get("key")), "blue"), fill=slots.get(str(item.get("key"))) == "ink")
        for item in series
    ]


def device_label(item: dict[str, Any]) -> str:
    key = str(item.get("key") or "").strip().lower()
    if not key or key == "unknown":
        return t("Unknown")
    if key == "other":
        return t("Other")
    if key == "mixed_unknown":
        return t("Mixed / Unknown")
    return str(item.get("label") or item.get("key"))


def percent_text(item: dict[str, Any]) -> str:
    try:
        percent = float(item.get("percent") or 0)
    except (TypeError, ValueError):
        percent = 0.0
    return f"{int(round(float(item.get('count') or 0)))} ({percent:.1f}%)"


class StatisticsPage(Page):
    page_key = "statistics"

    def build(self) -> None:
        self.range_value = str(self.context.ui_value(RANGE_SETTING_KEY, "24h") or "24h")
        if self.range_value not in dict(RANGES):
            self.range_value = "24h"
        self.shift = 0

        toolbar = Card(t("Statistics"), t("Statistics show APRS traffic structure observed by APRSBox. They are not a measure of audio quality or TNC decoding."))
        controls = QHBoxLayout()
        controls.addWidget(label(t("Range")))
        self.range_combo = combo(RANGES, self.range_value)
        self.range_combo.currentIndexChanged.connect(lambda _index: self._range_changed())
        controls.addWidget(self.range_combo)
        self.back_button = button(t("Back"), variant="secondary", icon_name="chevron-left", on_click=self._back)
        self.forward_button = button(t("Forward"), variant="secondary", icon_name="chevron-right", on_click=self._forward)
        controls.addWidget(self.back_button)
        controls.addWidget(self.forward_button)
        self.window_label = label("", "small")
        controls.addWidget(self.window_label)
        controls.addStretch(1)
        toolbar.add(controls)
        self.body.addWidget(toolbar)

        lists = QGridLayout()
        lists.setHorizontalSpacing(14)
        lists.setVerticalSpacing(14)
        self.direct_list = self._list_card(lists, 0, t("HEARD DIRECT"), f"({t('Max 20')})")
        self.users_list = self._list_card(lists, 1, t("TOP20 users"))
        self.devices_list = self._list_card(lists, 2, t("TOP20 devices"))
        for bar_list in (self.direct_list, self.users_list):
            bar_list.bar_clicked.connect(self._open_station)
        for column in range(3):
            lists.setColumnStretch(column, 1)
        self.body.addLayout(lists)

        frame_card = Card(t("APRS Frame Types"))
        self.frame_step = label("", "small")
        frame_card.add_action(self.frame_step)
        frame_row = QHBoxLayout()
        frame_row.setSpacing(16)
        self.frame_chart = LineChart(min_height=260)
        frame_row.addWidget(self.frame_chart, 3)
        self.frame_totals = BarList(slot="blue")
        self.frame_totals.empty_text = t("No data for selected range.")
        frame_row.addWidget(self.frame_totals, 2, Qt.AlignmentFlag.AlignTop)
        frame_card.add(frame_row)
        self.body.addWidget(frame_card)

        charts = QHBoxLayout()
        charts.setSpacing(14)
        heard_card = Card(t("Direct heard vs all heard"))
        self.heard_step = label("", "small")
        heard_card.add_action(self.heard_step)
        self.heard_chart = LineChart(min_height=260)
        heard_card.add(self.heard_chart)
        charts.addWidget(heard_card, 1)
        actions_card = Card(t("APRSBox Actions"))
        self.actions_step = label("", "small")
        actions_card.add_action(self.actions_step)
        self.actions_chart = LineChart(min_height=260)
        actions_card.add(self.actions_chart)
        charts.addWidget(actions_card, 1)
        self.body.addLayout(charts)
        for chart in (self.frame_chart, self.heard_chart, self.actions_chart):
            chart.empty_text = t("No data for selected range.")
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 60000
        self._sync_buttons()

    def _list_card(self, grid: QGridLayout, column: int, title: str, note: str = "") -> BarList:
        card = Card(title)
        if note:
            card.add_action(label(note, "small"))
        bar_list = BarList(slot="blue")
        bar_list.empty_text = t("No data for selected range.")
        card.add(bar_list)
        card.add(label(""), 1)
        grid.addWidget(card, 0, column)
        return bar_list

    # ------------------------------------------------------------- controls
    def _range_changed(self) -> None:
        self.range_value = str(combo_value(self.range_combo) or "24h")
        self.context.set_ui_value(RANGE_SETTING_KEY, self.range_value)
        self.shift = 0
        self._sync_buttons()
        self.refresh()

    def _back(self) -> None:
        self.shift += 1
        self._sync_buttons()
        self.refresh()

    def _forward(self) -> None:
        self.shift = max(0, self.shift - 1)
        self._sync_buttons()
        self.refresh()

    def _sync_buttons(self) -> None:
        self.forward_button.setEnabled(self.shift > 0)

    def _open_station(self, callsign: Any) -> None:
        if callsign:
            self.navigate("station-detail", str(callsign))

    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict) and argument.get("range") in dict(RANGES):
            set_combo_value(self.range_combo, argument["range"])

    # --------------------------------------------------------------- loading
    def refresh(self) -> None:
        self.loader.request()

    def _timer_tick(self) -> None:
        # Only the window ending "now" changes over time.
        if self.shift == 0:
            super()._timer_tick()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.radio_activity import (
            get_traffic_devices_statistics,
            get_traffic_direct_heard_statistics,
            get_traffic_statistics,
            get_traffic_users_statistics,
        )

        range_value, shift = self.range_value, self.shift
        with connection_scope():
            return {
                "request": (range_value, shift),
                "traffic": get_traffic_statistics(range_value=range_value, shift_windows=shift),
                "devices": get_traffic_devices_statistics(range_value=range_value, shift_windows=shift),
                "users": get_traffic_users_statistics(range_value=range_value, shift_windows=shift),
                "direct_heard": get_traffic_direct_heard_statistics(range_value=range_value, shift_windows=shift),
            }

    def _apply(self, payload: dict[str, Any]) -> None:
        if payload.get("request") != (self.range_value, self.shift):
            return  # A newer request is queued.
        traffic = payload.get("traffic") or {}
        self.window_label.setText(self._window_text(traffic))
        step = aggregation_text(traffic.get("bucket_minutes"))
        for widget in (self.frame_step, self.heard_step, self.actions_step):
            widget.setText(step)
        labels = [str(value) for value in traffic.get("labels") or []]
        charts = traffic.get("charts") or {}
        frame_series = list((charts.get("frame_types") or {}).get("series") or [])
        has_traffic = any(float(value or 0) > 0 for item in frame_series for value in item.get("data") or [])
        self.frame_chart.set_data(labels if has_traffic else [], frame_type_series(frame_series) if has_traffic else [])
        totals = sorted(
            ((sum(float(value or 0) for value in item.get("data") or []), item) for item in frame_series),
            key=lambda entry: -entry[0],
        )
        self.frame_totals.set_bars([Bar(t(item.get("label") or item.get("key")), total) for total, item in totals if total > 0])
        heard = list((charts.get("heard") or {}).get("series") or [])
        has_heard = any(float(value or 0) > 0 for item in heard for value in item.get("data") or [])
        self.heard_chart.set_data(labels if has_heard else [], slotted_series(heard, HEARD_SLOTS) if has_heard else [])
        actions = list((charts.get("actions") or {}).get("series") or [])
        has_actions = any(float(value or 0) > 0 for item in actions for value in item.get("data") or [])
        self.actions_chart.set_data(labels if has_actions else [], slotted_series(actions, ACTION_SLOTS) if has_actions else [])

        direct = payload.get("direct_heard") or {}
        self.direct_list.set_bars([
            Bar(f"{index}. {str(item.get('label') or item.get('key')).upper()}", float(item.get("count") or 0),
                value_text=percent_text(item), payload=str(item.get("key") or item.get("label") or "").upper())
            for index, item in enumerate(direct.get("items") or [], start=1) if float(item.get("count") or 0) > 0
        ])
        users = payload.get("users") or {}
        self.users_list.set_bars([
            Bar(f"{index}. {str(item.get('label') or item.get('key')).upper()}", float(item.get("count") or 0),
                value_text=percent_text(item), payload=str(item.get("key") or item.get("label") or "").upper())
            for index, item in enumerate(users.get("items") or [], start=1) if float(item.get("count") or 0) > 0
        ])
        devices = payload.get("devices") or {}
        device_bars = []
        for index, item in enumerate(devices.get("items") or [], start=1):
            if float(item.get("count") or 0) <= 0:
                continue
            stations = sorted({str(entry.get("callsign_ssid") or "").upper() for entry in item.get("entries") or [] if entry.get("callsign_ssid")})
            note = "\n".join([t("Counted unique CALLSIGN-SSID stations in this model"), *stations])
            device_bars.append(Bar(f"{index}. {device_label(item)}", float(item.get("count") or 0), note=note,
                                   value_text=percent_text(item)))
        self.devices_list.set_bars(device_bars)

    @staticmethod
    def _window_text(traffic: dict[str, Any]) -> str:
        from app.datetime_utils import format_display_datetime

        start = traffic.get("window_start_utc")
        end = traffic.get("window_end_utc")
        if not start or not end:
            return ""
        return f"{format_display_datetime(start)} – {format_display_datetime(end)}"
