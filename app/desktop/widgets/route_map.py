"""Route map of the traffic analyzer: who heard whom, arrows as thick as the frame count.

The user's choice (2026-11-22), after the map of SP9DLM's "remote gateway analysis" page: the hops of
the frames' paths drawn between the stations, here on APRSBox's own map with switchable layers:

* its frames to the receivers (ours, a remote receive page),
* the iGates that put its frames into APRS-IS (dashed),
* whose frames it repeats (when it is a digipeater),
* for the own station: the whole network, everything we heard on RF.

The data comes from ``station_analysis.analyze_station`` (``result["route_map"]``).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from app.desktop.i18n import t, tf
from app.desktop.map_layers import path_segment_color, station_markers
from app.desktop.rf_paths import KIND_APRSIS, KIND_RF_DIGI, KIND_RF_DIRECT
from app.desktop.station_analysis import MAP_APRSIS, MAP_DIGI, MAP_NETWORK, MAP_OWN, dist_km
from app.desktop.theme import current_colors
from app.desktop.widgets.common import checkbox, label
from app.desktop.widgets.map_view import (
    MapMarker,
    MapPolyline,
    MapView,
    escape_html,
    map_mask,
    overlay_contrast_color,
    stored_mask_percent,
)

LAYER_NAMES = {
    MAP_OWN: "Its frames to the receivers",
    MAP_APRSIS: "iGates (APRS-IS)",
    MAP_DIGI: "Whose frames it repeats",
    MAP_NETWORK: "The whole network (everything we hear)",
}
LAYER_CHECK_ORDER = (MAP_OWN, MAP_APRSIS, MAP_DIGI, MAP_NETWORK)
# The user's size (2026-11-22): the card's full width, the height 4/5 of it (width : height = 5 : 4).
HEIGHT_PER_WIDTH = 4 / 5
MAP_MIN_HEIGHT = 400
ARROW_AT = 0.62  # off the middle: the arrows of A -> B and B -> A do not cover each other
MISSING_SHOWN = 30
FIT_RADIUS_KM = 250  # the first view: the stations this close to the analysed one (the RF network around it)


def edge_width(count: Any) -> float:
    try:
        frames = max(0, int(count))
    except (TypeError, ValueError):
        frames = 0
    return round(min(7.0, 1.3 + math.log2(1 + frames) * 0.7), 2)


def edge_color(edge: dict[str, Any]) -> QColor:
    layer = edge.get("layer")
    if layer == MAP_APRSIS:
        return path_segment_color(KIND_APRSIS)
    if layer == MAP_DIGI:
        return current_colors().color("traffic-color-proxy-tx", "#dc9bf0")
    return path_segment_color(KIND_RF_DIRECT if edge.get("direct") else KIND_RF_DIGI)


def _when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%d.%m %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def edge_tooltip(edge: dict[str, Any]) -> str:
    bands = sorted((edge.get("bands") or {}).items(), key=lambda item: -item[1])
    frames = escape_html(tf("{count} frames", count=edge.get("n", 0)))
    if bands:
        frames += " (" + escape_html(", ".join(f"{name}: {count}" for name, count in bands)) + ")"
    rows = [
        f"<b>{escape_html(edge.get('a'))} → {escape_html(edge.get('b'))}</b>",
        escape_html(tf("{receiver} heard {sender}", receiver=edge.get("b"), sender=edge.get("a"))),
        escape_html(t(LAYER_NAMES.get(str(edge.get("layer")), str(edge.get("layer"))))),
        frames,
    ]
    if edge.get("last"):
        rows.append(f"<b>{escape_html(t('Last'))}:</b> {escape_html(_when(edge['last']))}")
    if edge.get("uncertain"):
        rows.append(f"<i>{escape_html(t('Through a digipeater without a callsign or position (bridged)'))}</i>")
    return "<br>".join(rows)


def edge_shapes(edges: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]) -> list[MapPolyline]:
    halo = overlay_contrast_color()
    halo.setAlphaF(0.30 if current_colors().is_dark else 0.45)
    shapes = []
    for edge in edges:
        a, b = nodes.get(edge.get("a")), nodes.get(edge.get("b"))
        if a is None or b is None:
            continue
        width = edge_width(edge.get("n"))
        shapes.append(MapPolyline(
            points=[(a["lat"], a["lon"]), (b["lat"], b["lon"])],
            color=edge_color(edge),
            width=width,
            halo=halo,
            halo_width=width + 2.0,
            dashed=bool(edge.get("uncertain")) or edge.get("layer") == MAP_APRSIS,
            tooltip=edge_tooltip(edge),
            payload=edge,
            opacity=0.9,
            arrows=True,
            arrow_at=ARROW_AT,
        ))
    return shapes


def node_markers(route_map: dict[str, Any], calls: set[str]) -> list[MapMarker]:
    """The stations at the ends of the drawn edges: the map's symbol when the map knows the station."""
    nodes = route_map.get("nodes") or {}
    marked = {str(route_map.get("focus") or ""), str(route_map.get("own") or ""), *(route_map.get("receivers") or [])}
    known, plain = [], []
    for call in sorted(calls):
        node = nodes.get(call)
        if node is None:
            continue
        station = node.get("station")
        if station:
            # The edges start where the analysis put the station (a mobile one: its newest position).
            known.append({**station, "latitude": node["lat"], "longitude": node["lon"]})
        else:
            plain.append(MapMarker(key=call, latitude=node["lat"], longitude=node["lon"], label=call,
                                   tooltip=f"<b>{escape_html(call)}</b><br>{escape_html(t('Position from its frames'))}",
                                   payload={"display_callsign": call}))
    markers = station_markers(known) + plain
    for marker in markers:
        if marker.key.upper() in marked or str(marker.key).upper().removesuffix("-0") in marked:
            marker.own = True  # the analysed station, our station and the receive pages: labels stand out
            marker.priority = 0
    return markers


def legend_html() -> str:
    def swatch(color: QColor, text: str, dashed: bool = False) -> str:
        mark = "┅" if dashed else "━"
        return f"<span style='color:{color.name()}; font-weight:700'>{mark}</span>&nbsp;{escape_html(text)}"

    return " &nbsp; ".join([
        swatch(path_segment_color(KIND_RF_DIRECT), t("into a receiver (us, a receive page)")),
        swatch(path_segment_color(KIND_RF_DIGI), t("to a digipeater")),
        swatch(path_segment_color(KIND_APRSIS), t("to an iGate (APRS-IS)"), dashed=True),
        swatch(current_colors().color("traffic-color-proxy-tx", "#dc9bf0"), t("frames it repeated")),
    ])


class RouteMapPanel(QWidget):
    """Layer switches, a legend, the map and the stations that could not be drawn."""

    def __init__(self, route_map: dict[str, Any], *, context: Any = None,
                 on_open_station: Callable[[str], None] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.route_map = route_map
        self._on_open_station = on_open_station
        self._fit_pending = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        counts = route_map.get("layers") or {}
        switches = QHBoxLayout()
        self.layer_checks = {}
        for layer in LAYER_CHECK_ORDER:
            if not counts.get(layer):
                continue
            box = checkbox(t(LAYER_NAMES[layer]), True)
            box.setToolTip(tf("{count} arrows", count=counts[layer]))
            box.toggled.connect(lambda _checked: self._redraw(fit=True))
            self.layer_checks[layer] = box
            switches.addWidget(box)
        switches.addStretch(1)
        layout.addLayout(switches)
        legend = label(legend_html(), "small", wrap=True)
        legend.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(legend)
        layout.addWidget(label(t("Arrow A → B: B heard A's transmission; the thicker, the more frames. Dashed: through a "
                                 "digipeater without a callsign or position. Double click a station: its card."), "small", wrap=True))
        self.view = MapView(self, show_zoom_control=True)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.view.setFixedHeight(MAP_MIN_HEIGHT)  # until the card is laid out; then 4/5 of the width
        try:
            from app.services.content import get_aprs_symbol_set

            self.view.set_modern_symbols(get_aprs_symbol_set() == "modern")
        except Exception:  # noqa: BLE001 - classic symbols then
            pass
        if context is not None:
            self.view.set_mask(*map_mask(stored_mask_percent(context, self.view.tiles.source.source_id,
                                                             dark=current_colors().is_dark)))
        self.view.marker_clicked.connect(lambda _payload: None)  # one click: the info window only
        self.view.marker_double_clicked.connect(self._open)
        self.view.installEventFilter(self)
        layout.addWidget(self.view)
        missing = list(route_map.get("missing") or [])
        if missing:
            shown = ", ".join(missing[:MISSING_SHOWN]) + (f" (+{len(missing) - MISSING_SHOWN})" if len(missing) > MISSING_SHOWN else "")
            layout.addWidget(label(tf("Not on the map, no known position: {calls}", calls=shown), "muted", wrap=True))
        self._redraw(fit=False)

    # ------------------------------------------------------------------ drawing
    def visible_layers(self) -> set[str]:
        return {layer for layer, box in self.layer_checks.items() if box.isChecked()}

    def visible_edges(self) -> list[dict[str, Any]]:
        layers = self.visible_layers()
        return [edge for edge in self.route_map.get("edges") or [] if edge.get("layer") in layers]

    def _calls(self, edges: list[dict[str, Any]]) -> set[str]:
        calls = {str(edge["a"]) for edge in edges} | {str(edge["b"]) for edge in edges}
        focus = str(self.route_map.get("focus") or "")
        if focus in (self.route_map.get("nodes") or {}):
            calls.add(focus)
        return calls

    def _redraw(self, *, fit: bool) -> None:
        edges = self.visible_edges()
        nodes = self.route_map.get("nodes") or {}
        self.view.set_layer("paths", edge_shapes(edges, nodes))
        self.view.set_markers(node_markers(self.route_map, self._calls(edges)))
        if fit:
            self._fit()

    def _fit(self) -> None:
        nodes = self.route_map.get("nodes") or {}
        points = [(nodes[call]["lat"], nodes[call]["lon"]) for call in self._calls(self.visible_edges()) if call in nodes]
        if not points:
            return
        center = nodes.get(str(self.route_map.get("focus") or "")) or nodes.get(str(self.route_map.get("own") or ""))
        if center is not None:
            # Far stations (e.g. internet frames a gateway put on the air with their original path) stay drawn,
            # but the first view shows the region around the analysed station.
            near = [point for point in points if dist_km(center["lat"], center["lon"], point[0], point[1]) <= FIT_RADIUS_KM]
            if len(near) >= 2:
                points = near
        if len(points) == 1:
            self.view.set_view(points[0][0], points[0][1], 11)
        else:
            self.view.fit_points(points, padding=48, max_zoom=13)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if watched is self.view and event.type() == QEvent.Type.Resize:
            height = max(MAP_MIN_HEIGHT, round(self.view.width() * HEIGHT_PER_WIDTH))
            if self.view.height() != height:
                self.view.setFixedHeight(height)  # the width follows the card, the height follows the width
            elif self._fit_pending and self.view.width() > 300:
                # The first fit needs the map's real size, known only once the card is laid out.
                self._fit_pending = False
                QTimer.singleShot(0, self._fit)
        return super().eventFilter(watched, event)

    def _open(self, payload: Any) -> None:
        if self._on_open_station is None or not isinstance(payload, dict):
            return
        callsign = payload.get("display_callsign") or payload.get("callsign")
        if callsign:
            self._on_open_station(str(callsign))
