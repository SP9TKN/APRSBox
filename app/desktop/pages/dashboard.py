"""Dashboard page (web: templates/dashboard.html)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors
from app.desktop.widgets.charts import LineChart, Series, format_number
from app.desktop.widgets.common import (
    Card,
    Page,
    ToneLabel,
    button,
    clear_layout,
    icon_button,
    label,
    tone_color,
)

ACTIVITY_RANGES = ("1h", "3h", "6h", "12h", "24h", "7d", "30d", "365d")
REFRESH_MS = 30000


def station_health(home: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    """Port of the Jinja logic at the top of dashboard.html."""
    station = {"callsign": "Not set", "rx_tone": "neutral", "rx_value": "Unknown", "tx_tone": "neutral",
               "tx_value": "Unknown", "aprsis_tone": "neutral", "aprsis_value": "Not configured"}
    for item in home.get("hero_summary") or []:
        name = item.get("label")
        if name == "Callsign":
            station["callsign"] = item.get("value")
        elif name == "RF RX":
            station["rx_tone"], station["rx_value"] = item.get("tone"), item.get("value")
        elif name == "RF TX":
            station["tx_tone"], station["tx_value"] = item.get("tone"), item.get("value")
        elif name == "APRS-IS":
            station["aprsis_tone"], station["aprsis_value"] = item.get("tone"), item.get("value")
    checks = home.get("checks") or []
    runtime_state = checks[0].get("state") if checks else "warn"
    label_text, tone = "DEGRADED", "warn"
    if runtime_state == "error" or station["rx_value"] == "Stale":
        label_text, tone = "OFFLINE / ATTENTION", "error"
    elif station["rx_tone"] == "ok" and runtime_state == "ok":
        label_text, tone = "ONLINE", "ok"
    return station, label_text, tone


def normalize_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Port of dashboard.html normalizeApiPayload."""
    labels = list(payload.get("labels") or [])
    series = payload.get("series") or {}

    def values(key: str) -> list[float]:
        raw = list(series.get(key) or [])
        return [float(raw[index] or 0) if index < len(raw) else 0.0 for index in range(len(labels))]

    rx = values("rx_total")
    tx = values("tx_total")
    repeated = values("digipeated_total")
    uplink = values("gated_to_aprsis_total")
    total = [a + b for a, b in zip(rx, tx)]
    kpis = payload.get("kpis") or {}
    return {
        "range": str(payload.get("range") or ""),
        "output_bucket_minutes": int(payload.get("output_bucket_minutes") or 5),
        "labels": labels,
        "bucket_starts": list(payload.get("bucket_starts_utc") or []),
        "series": {"rx": rx, "tx": tx, "repeated_tx": repeated, "aprsis_uplink": uplink, "total": total},
        "totals": {"total": sum(total), "rx": sum(rx), "tx": sum(tx), "repeated_tx": sum(repeated)},
        "kpis": {"heard_stations": int(kpis.get("heard_stations") or 0), "aprs_frames": int(kpis.get("aprs_frames") or 0)},
    }


class DashboardPage(Page):
    page_key = "dashboard"

    def build(self) -> None:
        context = self.context
        self.range_value = str(context.ui_value("dashboard/activity_range", "24h") or "24h")
        if self.range_value not in ACTIVITY_RANGES:
            self.range_value = "24h"
        try:
            self.zoom_by_range = json.loads(str(context.ui_value("dashboard/activity_zoom", "{}") or "{}"))
        except ValueError:
            self.zoom_by_range = {}

        top = QHBoxLayout()
        top.setSpacing(14)
        # Station status ----------------------------------------------------
        self.station_card = Card()
        stage = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(4)
        copy.addWidget(label(t("Station status"), "eyebrow"))
        self.health_label = label("", "kpi")
        copy.addWidget(self.health_label)
        identity = QHBoxLayout()
        identity.addWidget(label(t("Callsign"), "muted"))
        self.callsign_label = label("", "h2")
        identity.addWidget(self.callsign_label)
        identity.addStretch(1)
        copy.addLayout(identity)
        stage.addLayout(copy, 1)
        self.tower = QLabel()
        stage.addWidget(self.tower, 0, Qt.AlignmentFlag.AlignTop)
        self.station_card.add(stage)
        links = QHBoxLayout()
        links.setSpacing(10)
        self.link_states: dict[str, ToneLabel] = {}
        for key, caption, icon_name in (
            ("rx", "RF RX", "download-circle-outline"),
            ("tx", "RF TX", "upload-circle-outline"),
            # This state is the iGate uplink (frames sent to APRS-IS), not the connection itself:
            # "Not configured" means no routing rule targets APRS-IS (as checks name it).
            ("aprsis", "APRS-IS iGate", "lan-connect"),
        ):
            tile = QFrame()
            tile.setProperty("tile", True)
            if key == "aprsis":
                tile.setToolTip(t("Frames sent to APRS-IS by the iGate. It needs an enabled routing rule with APRS-IS as the target. The connection itself is shown under Station Readiness → APRS-IS connection."))
            tile_layout = QHBoxLayout(tile)
            tile_layout.setContentsMargins(10, 8, 10, 8)
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap(icon_name, current_colors().color("accent"), 22))
            tile_layout.addWidget(icon_label)
            text_layout = QVBoxLayout()
            text_layout.setSpacing(0)
            text_layout.addWidget(label(caption, "small"))
            state = ToneLabel("-", "neutral")
            text_layout.addWidget(state)
            tile_layout.addLayout(text_layout, 1)
            self.link_states[key] = state
            links.addWidget(tile, 1)
        self.station_card.add(links)
        top.addWidget(self.station_card, 3)

        self.band_host = QWidget()
        self.band_layout = QVBoxLayout(self.band_host)
        self.band_layout.setContentsMargins(0, 0, 0, 0)
        self.band_layout.setSpacing(8)
        top.addWidget(self.band_host, 1)

        # Network ---------------------------------------------------------------
        self.network_card = Card(t("Network"))
        self.network_grid = QGridLayout()
        self.network_grid.setHorizontalSpacing(14)
        self.network_grid.setVerticalSpacing(6)
        self.network_card.add(self.network_grid)
        top.addWidget(self.network_card, 2)
        self.body.addLayout(top)

        # KPI strip -------------------------------------------------------------
        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        self.kpi_values: dict[str, tuple[QLabel, QLabel]] = {}
        for key, caption, icon_name in (
            ("heard", t("Heard stations"), "account-multiple"),
            ("frames", t("APRS frames"), "file-document-outline"),
            ("interfaces", t("Interfaces"), "tune-variant"),
        ):
            tile = QFrame()
            tile.setProperty("card", "true")
            tile_layout = QHBoxLayout(tile)
            tile_layout.setContentsMargins(14, 12, 14, 12)
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap(icon_name, current_colors().color("accent"), 28))
            tile_layout.addWidget(icon_label)
            texts = QVBoxLayout()
            texts.setSpacing(0)
            texts.addWidget(label(caption, "muted"))
            value = label("-", "kpi")
            texts.addWidget(value)
            meta = label("", "small")
            texts.addWidget(meta)
            tile_layout.addLayout(texts, 1)
            self.kpi_values[key] = (value, meta)
            kpis.addWidget(tile, 1)
        self.body.addLayout(kpis)

        # Activity + readiness -------------------------------------------------------
        middle = QHBoxLayout()
        middle.setSpacing(14)
        self.activity_card = Card(t("Activity Charts"))
        self.range_combo = QComboBox()
        for value in ACTIVITY_RANGES:
            self.range_combo.addItem(value, value)
        self.range_combo.setCurrentIndex(ACTIVITY_RANGES.index(self.range_value))
        self.range_combo.currentIndexChanged.connect(self._range_changed)
        self.activity_card.add_action(label(t("Range"), "muted"))
        self.activity_card.add_action(self.range_combo)
        hint_row = QHBoxLayout()
        hint_row.addWidget(label(t("RX / TX / repeats / APRS-IS uplink"), "h3"))
        self.window_label = label("", "small")
        self.step_label = label("", "small")
        hint_row.addWidget(self.window_label)
        hint_row.addWidget(self.step_label)
        hint_row.addStretch(1)
        hint_row.addWidget(label(t("Drag to zoom, double click to reset."), "small"))
        self.activity_card.add(hint_row)
        self.chart = LineChart(min_height=260)
        self.chart.empty_text = t("Recent activity data will appear here.")
        self.chart.zoom_changed.connect(self._zoom_changed)
        self.activity_card.add(self.chart, 1)
        totals = QHBoxLayout()
        self.total_labels: dict[str, QLabel] = {}
        for key, caption in (("total", t("All frames")), ("rx", t("Received (RX)")), ("tx", t("Sent (TX)")),
                             ("repeated_tx", t("Repeated TX"))):
            box = QVBoxLayout()
            box.setSpacing(0)
            box.addWidget(label(caption, "small"))
            value = label("0", "kpi-small")
            box.addWidget(value)
            self.total_labels[key] = value
            totals.addLayout(box)
        totals.addStretch(1)
        totals.addWidget(button(t("Open detailed statistics"), variant="secondary", on_click=lambda: self.navigate("statistics")))
        self.activity_card.add(totals)
        middle.addWidget(self.activity_card, 3)

        self.readiness_card = Card(t("Station Readiness"))
        self.readiness_card.add_action(icon_button("help-circle-outline", t("Help"),
                                                   lambda: show_help(self, "application/dashboard_readiness")))
        self.readiness_overview = QGridLayout()
        self.readiness_overview.setHorizontalSpacing(12)
        self.readiness_overview.setVerticalSpacing(6)
        self.readiness_card.add(self.readiness_overview)
        self.matrix = QGridLayout()
        self.matrix.setHorizontalSpacing(12)
        self.matrix.setVerticalSpacing(6)
        self.readiness_card.add(self.matrix)
        self.readiness_card.body.addStretch(1)
        middle.addWidget(self.readiness_card, 2)
        self.body.addLayout(middle)
        self.body.addStretch(1)

        self.loader = SerialLoader(self._load, self._apply, self)
        self.activity_loader = SerialLoader(self._load_activity, self._apply_activity, self)
        self.auto_refresh_ms = REFRESH_MS

    # --------------------------------------------------------------- loading
    def refresh(self) -> None:
        self.loader.request()
        self.activity_loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.desktop.netinfo import network_snapshot
        from app.services.band_condition import get_dashboard_band_condition_snapshot
        from app.services.content import dashboard_home_data
        from app.services.radio_activity import get_dashboard_radio_activity

        with connection_scope():
            bands = list((get_dashboard_band_condition_snapshot() or {}).get("bands") or [])
            preferred = next((item for item in bands if item.get("band") == "2m"), None) or (bands[0] if bands else None)
            activity = get_dashboard_radio_activity(range_value="24h")
            home = dashboard_home_data(preferred, activity)
        return {"bands": bands, "home": home, "network": network_snapshot()}

    def _load_activity(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.radio_activity import get_dashboard_radio_activity

        with connection_scope():
            return get_dashboard_radio_activity(range_value=self.range_value)

    # -------------------------------------------------------------- applying
    def _apply(self, result: dict[str, Any]) -> None:
        home = result.get("home") or {}
        station, health_text, health_tone = station_health(home)
        self.health_label.setText(t(health_text))
        self.health_label.setStyleSheet(f"color: {tone_color(health_tone).name()};")
        callsign = station.get("callsign") or "Not set"
        self.callsign_label.setText(t("Not set") if callsign == "Not set" else str(callsign))
        self.tower.setPixmap(icon_pixmap("radio-tower", tone_color(health_tone), 54))
        self.link_states["rx"].set_state(t(str(station["rx_value"])), str(station["rx_tone"]))
        self.link_states["tx"].set_state(t(str(station["tx_value"])), str(station["tx_tone"]))
        self.link_states["aprsis"].set_state(t(str(station["aprsis_value"])), str(station["aprsis_tone"]))
        stats = {item.get("label"): item.get("value") for item in home.get("stats") or []}
        self.kpi_values["interfaces"][0].setText(str(stats.get("Interfaces") or "-"))
        self._render_bands(result.get("bands") or [])
        self._render_network(result.get("network") or {})
        self._render_readiness(home.get("station_readiness") or {})

    def _render_bands(self, bands: list[dict[str, Any]]) -> None:
        clear_layout(self.band_layout)
        self.band_host.setVisible(bool(bands))
        for item in bands:
            card = QFrame()
            card.setProperty("card", "true")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(2)
            tone = str(item.get("diagnosis_tone") or "neutral")
            layout.addWidget(label(str(item.get("band_label") or item.get("band") or ""), "muted"))
            index = item.get("condition_index")
            value = label("W–" if index is None else f"W{index}", "kpi")
            value.setStyleSheet(f"color: {tone_color(tone).name()};")
            layout.addWidget(value)
            layout.addWidget(label(t(str(item.get("label") or "")), "small", wrap=True))
            card.setToolTip(f"{item.get('interface_name') or ''} — {t(str(item.get('label') or ''))}")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.mousePressEvent = lambda _event: self.navigate("band-condition")  # type: ignore[method-assign]
            self.band_layout.addWidget(card)
        self.band_layout.addStretch(1)

    def _render_network(self, network: dict[str, Any]) -> None:
        clear_layout(self.network_grid)
        unavailable = t("Unavailable")
        rows = [
            (t("Hostname"), network.get("hostname") or unavailable),
            (t("Primary IPv4 LAN"), network.get("ipv4") or unavailable),
            ("IPv6", network.get("ipv6") or unavailable),
            (t("mDNS name"), network.get("mdns_name") or unavailable),
        ]
        ports = network.get("kiss_ports") or []
        if ports:
            text = "\n".join(
                f"{item['endpoint']}  ({item['name']}{'' if item['enabled'] else ', ' + t('Disabled')})" for item in ports
            )
        else:
            text = t("No shared KISS ports")
        rows.append((t("Shared KISS TCP ports"), text))
        for index, (caption, value) in enumerate(rows):
            self.network_grid.addWidget(label(caption, "muted"), index, 0, Qt.AlignmentFlag.AlignTop)
            self.network_grid.addWidget(label(str(value), None, wrap=True, selectable=True), index, 1)
        self.network_grid.setColumnStretch(1, 1)

    def _render_readiness(self, readiness: dict[str, Any]) -> None:
        clear_layout(self.readiness_overview)
        for row, entry in enumerate(readiness.get("overview") or []):
            if entry.get("status_key"):
                status = tf(entry.get("status_key"), **(entry.get("status_params") or {}))
            else:
                status = t(str(entry.get("status") or ""))
            self.readiness_overview.addWidget(label(t(str(entry.get("label") or "")), "muted"), row, 0)
            self.readiness_overview.addWidget(ToneLabel(status, str(entry.get("tone") or "neutral")), row, 1)
        self.readiness_overview.setColumnStretch(1, 1)
        clear_layout(self.matrix)
        headers = (t("Interface"), "RF → IS", "IS → RF", "RF → RF")
        for column, header in enumerate(headers):
            self.matrix.addWidget(label(header, "eyebrow"), 0, column)
        interfaces = readiness.get("interfaces") or []
        if not interfaces:
            self.matrix.addWidget(label(t("No radio interfaces configured."), "muted"), 1, 0, 1, 4)
        for row, interface in enumerate(interfaces, start=1):
            name = label(str(interface.get("name") or ""), "h3")
            self.matrix.addWidget(name, row, 0)
            if not interface.get("enabled"):
                self.matrix.addWidget(ToneLabel(t("Disabled"), "neutral", bold=False), row, 1)
                self.matrix.addWidget(ToneLabel("", "neutral"), row, 2)
                self.matrix.addWidget(ToneLabel("", "neutral"), row, 3)
                name.setEnabled(False)
                continue
            to_aprsis = bool(interface.get("to_aprsis"))
            cell = ToneLabel(t("Configured") if to_aprsis else t("Missing"), "ok" if to_aprsis else "warn", bold=False)
            cell.setToolTip(f"{t('Interface → APRS-IS')}: {t('Configured') if to_aprsis else t('Missing')}")
            self.matrix.addWidget(cell, row, 1)
            if interface.get("tx_capable"):
                from_aprsis = bool(interface.get("from_aprsis"))
                cell = ToneLabel(t("Configured") if from_aprsis else t("Missing"), "ok" if from_aprsis else "warn", bold=False)
                cell.setToolTip(f"{t('APRS-IS → interface')}: {t('Configured') if from_aprsis else t('Missing')}")
            else:
                cell = ToneLabel(t("Receive-only interface"), "neutral", bold=False)
            self.matrix.addWidget(cell, row, 2)
            rf_ready = bool(interface.get("rf_ready"))
            cell = ToneLabel(f"{interface.get('rf_target_count') or 0} / {interface.get('rf_target_total') or 0}",
                             "ok" if rf_ready else "warn", bold=False)
            cell.setToolTip(f"{t('Interface → interface')}: {interface.get('rf_target_count') or 0} / {interface.get('rf_target_total') or 0}")
            self.matrix.addWidget(cell, row, 3)
        self.matrix.setColumnStretch(0, 1)

    def _apply_activity(self, payload: dict[str, Any]) -> None:
        data = normalize_activity(payload or {})
        series = data["series"]
        chart_series = [
            Series("rx", t("Received (RX)"), series["rx"], slot="ink", fill=True),
            Series("tx", t("Sent (TX)"), series["tx"], slot="blue"),
            Series("repeated_tx", t("Repeated TX"), series["repeated_tx"], slot="orange"),
            Series("aprsis_uplink", t("APRS-IS uplink"), series["aprsis_uplink"], slot="aqua"),
        ]
        tooltip_labels = []
        for index, text in enumerate(data["labels"]):
            start = data["bucket_starts"][index] if index < len(data["bucket_starts"]) else ""
            tooltip_labels.append(self._format_bucket(start) or text)
        self.chart.set_data(data["labels"], chart_series, tooltip_labels=tooltip_labels)
        zoom = self.zoom_by_range.get(self.range_value)
        if isinstance(zoom, dict):
            self.chart.set_zoom(int(zoom.get("min", 0)), int(zoom.get("max", 0)))
        for key, value_label in self.total_labels.items():
            value_label.setText(format_number(data["totals"].get(key, 0)))
        minutes = int(payload.get("range_minutes") or 0)
        self.window_label.setText(f"{minutes} {t('min')}" if minutes else "")
        self.step_label.setText(f"{data['output_bucket_minutes']} {t('min step')}")
        self.kpi_values["heard"][0].setText(format_number(data["kpis"]["heard_stations"]))
        self.kpi_values["frames"][0].setText(format_number(data["kpis"]["aprs_frames"]))
        range_text = f"{t('Range')}: {self.range_value}"
        self.kpi_values["heard"][1].setText(range_text)
        self.kpi_values["frames"][1].setText(range_text)

    @staticmethod
    def _format_bucket(value: str) -> str:
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
        return parsed.strftime("%Y.%m.%d %H:%M UTC")

    def _range_changed(self) -> None:
        value = self.range_combo.currentData()
        if not value:
            return
        self.range_value = str(value)
        self.context.set_ui_value("dashboard/activity_range", self.range_value)
        self.chart.set_loading(True)
        self.activity_loader.request()

    def _zoom_changed(self, start: int, end: int) -> None:
        if end - start + 1 < 4 or (start == 0 and end >= len(self.chart.labels) - 1):
            self.zoom_by_range.pop(self.range_value, None)
        else:
            self.zoom_by_range[self.range_value] = {"min": start, "max": end}
        self.context.set_ui_value("dashboard/activity_zoom", json.dumps(self.zoom_by_range))
