"""Turns map payloads (app.services.map_service) into MapView shapes.

Shared by the Map page and the small maps on detail pages so markers, tracks,
coverage and alert areas look the same everywhere (as in the web GUI).
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Callable

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QToolTip

from app.desktop import geo
from app.desktop.i18n import t
from app.desktop.rf_paths import (
    KIND_APRSIS,
    KIND_INTERNET,
    KIND_RF_DIGI,
    KIND_RF_DIRECT,
    VIA_IGATE,
    VIA_INTERNET,
    VIA_RF,
    PathSegment,
    frame_reception,
    object_owner,
    own_station_identity,
    reception_segments,
)
from app.desktop.theme import current_colors
from app.desktop.widgets.map_view import (
    MapMarker,
    MapView,
    MapPolygon,
    MapPolyline,
    alert_area_color,
    callsign_color,
    escape_html,
    geojson_polygons,
    overlay_contrast_color,
)
from app.datetime_utils import format_display_datetime

MOBILE_TRACK_MAX_RENDERED_POINTS = 60
OWN_SETTINGS_ORIGIN = "own_settings"


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_age(seconds: Any) -> str:
    value = parse_float(seconds)
    if value is None or value < 0:
        return ""
    if value < 60:
        return t("{n} s ago").format(n=round(value))
    if value < 3600:
        return t("{n} min ago").format(n=round(value / 60))
    if value < 86400:
        return t("{n} h ago").format(n=round(value / 3600))
    return t("{n} d ago").format(n=round(value / 86400))


def heard_time_rows(station: dict[str, Any]) -> list[tuple[str, str]]:
    """When the own radio and when APRS-IS last had the station, each with its age (the user: the
    map keeps a station heard over APRS-IS, so one "last heard" time of the radio looked hours old)."""
    rows: list[tuple[str, str]] = []
    for label, key in (("Radio", "last_heard_rf_at"), ("APRS-IS", "last_seen_aprsis_at")):
        value = station.get(key)
        if not value:
            continue
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = (datetime.now(timezone.utc) - moment).total_seconds()
        if 3600 <= seconds < 86400:
            minutes = int(seconds // 60)
            age = t("{h} h {m} min ago").format(h=minutes // 60, m=minutes % 60)
        else:
            age = format_age(seconds)
        rows.append((t(label), format_display_datetime(value) + (f" ({age})" if age else "")))
    return rows


def symbol_overlay(symbol_table: Any) -> str:
    text = str(symbol_table or "").strip()
    if not text or text in {"/", "\\"}:
        return ""
    return text[:1]


def pick_hint_row() -> str:
    return f"<i>{escape_html(t('Right click: line to the station that received it'))}</i>"


def can_draw_reception(line: str, source_kind: str) -> bool:
    """A right click can draw where this frame went: it was heard on RF (by us or an iGate)."""
    reception = frame_reception(line, source_kind=source_kind, own_callsign="")
    return reception is not None and reception.via in (VIA_RF, VIA_IGATE)


def station_reception_payload(station: Any) -> dict[str, Any] | None:
    """A map station (marker payload) as the track point of its latest frame, for TrackReceptionLines."""
    if not isinstance(station, dict):
        return None
    line = str(station.get("raw_text") or station.get("raw_latest_packet") or "")
    if not line:
        return None
    return {
        "latitude": station.get("latitude"),
        "longitude": station.get("longitude"),
        "line": line,
        "source_kind": str(station.get("raw_source_kind") or station.get("source_kind") or ""),
        "heard_at": str(station.get("last_heard_at") or ""),
        "display_callsign": str(station.get("display_callsign") or station.get("callsign") or ""),
        "sender": object_owner(line),  # objects and items: the line starts at the station that sent them
    }


# Station tooltips stay about as wide as their ordinary rows (the user's choice): the decoded data
# and a long comment wrap to that width, in characters, instead of stretching across the map.
TOOLTIP_MIN_CHARS = 56
TOOLTIP_MAX_CHARS = 72
# Weather goes on 2-3 lines by topic (labels as app.services.content decodes them).
_WX_AIR = ("Temperatura", "Wilgotność", "Ciśnienie")
_WX_WIND = ("Kierunek wiatru", "Prędkość wiatru", "Porywy")
_WX_SUN = ("Nasłonecznienie", "Promieniowanie")
_WX_RAIN = ("Deszcz 1h", "Deszcz 24h", "Deszcz od północy")


def _wrap_parts(parts: list[str], width: int, separator: str = " · ") -> list[str]:
    lines: list[str] = []
    for part in parts:
        if lines and len(lines[-1]) + len(separator) + len(part) <= width:
            lines[-1] += separator + part
        else:
            lines.append(part)
    return lines


def station_data_lines(data: Any, width: int = TOOLTIP_MIN_CHARS) -> list[str]:
    """The decoded data of a station as short lines: weather by topic, the rest wrapped to ``width``."""
    values: dict[str, str] = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("label") and item.get("value"):
            values.setdefault(str(item["label"]), str(item["value"]))

    def take(labels: tuple[str, ...]) -> list[tuple[str, str]]:
        return [(label, values.pop(label)) for label in labels if label in values]

    topic_width = max(width, TOOLTIP_MAX_CHARS)
    lines = _wrap_parts([f"{label}: {value}" for label, value in take(_WX_AIR)], topic_width)
    wind = dict(take(_WX_WIND))
    wind_parts = [value for label, value in wind.items() if label != "Porywy"]
    if "Porywy" in wind:
        wind_parts.append(f"{t('gusts')} {wind['Porywy']}")
    sun_line = [f"{t('Wind')}: " + ", ".join(wind_parts)] if wind_parts else []
    sun_line += [f"{label}: {value}" for label, value in take(_WX_SUN)]
    lines += _wrap_parts(sun_line, topic_width)
    rain_names = {"Deszcz 1h": "1h", "Deszcz 24h": "24h", "Deszcz od północy": t("since midnight")}
    rain = take(_WX_RAIN)
    if rain:
        lines.append(f"{t('Rain')}: " + ", ".join(f"{rain_names[label]} {value}" for label, value in rain))
    lines += _wrap_parts([f"{label}: {value}" for label, value in values.items()], width)
    return lines


def station_tooltip(station: dict[str, Any], *, pick_hint: bool = False) -> str:
    title = str(station.get("display_callsign") or station.get("callsign") or "")
    fields: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value not in (None, ""):
            fields.append((label, str(value)))

    add(t("APRS client"), station.get("aprs_device_short"))
    heard_rows = heard_time_rows(station)
    for label, value in heard_rows:
        add(label, value)
    heard_at = station.get("last_heard_at")
    if heard_at and not heard_rows:
        add(t(station.get("activity_label") or "Last activity"), format_display_datetime(heard_at))
    age = format_age(station.get("last_heard_age_s"))
    if age and not heard_rows:
        add(t(station.get("activity_age_label") or "Age"), age)
    add(t("Source"), station.get("source"))
    add(t("Path"), station.get("path"))
    distance = parse_float(station.get("distance_km"))
    if distance is not None:
        add(t("Distance"), f"{distance} km")
    comment = str(station.get("comment") or "")
    altitude = parse_float(station.get("altitude"))
    width = max([TOOLTIP_MIN_CHARS] + [len(label) + 2 + len(value) for label, value in fields])
    width = min(width, TOOLTIP_MAX_CHARS)
    data_lines = station_data_lines(station.get("data"), width)
    width = min(max([width] + [len(line) for line in data_lines]), TOOLTIP_MAX_CHARS)  # the comment may use it all
    rows = [f"<b style='font-size:11pt'>{escape_html(title)}</b>"]
    rows += [f"<b>{escape_html(label)}:</b> {escape_html(value)}" for label, value in fields]
    if comment:
        label = t("Comment")
        words = _wrap_parts(comment.split(), width - len(label) - 2, " ") or [comment]
        rows.append(f"<b>{escape_html(label)}:</b> " + "<br>".join(escape_html(line) for line in words))
    if altitude is not None:
        rows.append(f"<b>{escape_html(t('Altitude'))}:</b> {int(altitude)} m")
    if data_lines:
        rows.append("<span style='color:#888'>" + "<br>".join(escape_html(line) for line in data_lines) + "</span>")
    if pick_hint:
        rows.append(pick_hint_row())
    return "<div style='white-space:nowrap'>" + "<br>".join(rows) + "</div>"


def station_markers(stations: list[dict[str, Any]], *, own_key: str = "", pick_hint: bool = False) -> list[MapMarker]:
    """Station markers; ``pick_hint`` when a right click draws the way of a station's latest frame."""
    markers: list[MapMarker] = []
    own_key = own_key.upper()
    for index, station in enumerate(stations):
        latitude = parse_float(station.get("latitude"))
        longitude = parse_float(station.get("longitude"))
        if latitude is None or longitude is None:
            continue
        key = str(station.get("display_callsign") or station.get("callsign") or "").strip()
        if not key:
            continue
        priority = 2
        if station.get("origin") == "local_tx":
            priority = 0
        elif station.get("is_rf"):
            priority = 1
        own = bool(own_key) and key.upper() == own_key
        latest = station_reception_payload(station) if pick_hint and not own and priority else None
        markers.append(
            MapMarker(
                key=key,
                latitude=latitude,
                longitude=longitude,
                label=key,
                icon_path=str(station.get("symbol_icon") or ""),
                overlay=symbol_overlay(station.get("symbol_table")),
                stale=bool(station.get("stale")),
                tooltip=station_tooltip(station, pick_hint=latest is not None
                                        and can_draw_reception(latest["line"], latest["source_kind"])),
                payload=station,
                priority=priority * 100000 + index,
                own=own,
            )
        )
    return markers


def own_station_marker(station_settings: dict[str, Any], *, symbol_icon: str, present_keys: set[str]) -> MapMarker | None:
    """The own station at its My Station position, the way APRSIS32 always shows it.

    Only while the map payload has no position of it yet (no frame of it sent or
    heard); after that the regular marker from the payload takes over.
    """
    key = own_station_identity(station_settings)
    latitude = parse_float(station_settings.get("latitude"))
    longitude = parse_float(station_settings.get("longitude"))
    if not key or latitude is None or longitude is None or key in present_keys:
        return None
    entry = {
        "display_callsign": key,
        "origin": OWN_SETTINGS_ORIGIN,
        "source": t("My Station settings (no frames yet)"),
        "comment": str(station_settings.get("beacon_comment") or "").strip(),
    }
    overlay = str(station_settings.get("symbol_overlay") or "").strip()[:1] if station_settings.get("symbol_table") == "\\" else ""
    return MapMarker(
        key=key,
        latitude=latitude,
        longitude=longitude,
        label=key,
        icon_path=symbol_icon,
        overlay=overlay,
        tooltip=station_tooltip(entry),
        payload=entry,
        priority=0,
        own=True,
    )


def coverage_shapes(stations: list[dict[str, Any]], *, fill_opacity: float, outline_opacity: float = 1.0) -> list[MapPolygon]:
    shapes: list[MapPolygon] = []
    for station in stations:
        latitude = parse_float(station.get("latitude"))
        longitude = parse_float(station.get("longitude"))
        range_km = parse_float(station.get("phg_range_km"))
        if latitude is None or longitude is None or range_km is None or range_km <= 0:
            continue
        color = callsign_color(str(station.get("display_callsign") or station.get("callsign") or ""))
        radius_m = range_km * 1000
        azimuth = geo.phg_direction_azimuth(station.get("phg_direction"))
        if azimuth is None:
            ring = geo.circle_points(latitude, longitude, radius_m)
        else:
            ring = geo.phg_cardioid_points(latitude, longitude, azimuth, radius_m)
        shapes.append(
            MapPolygon(
                rings=[ring],
                stroke=color,
                fill=color,
                width=1.5,
                stroke_opacity=outline_opacity,
                fill_opacity=fill_opacity,
            )
        )
    return shapes


def point_reception_parts(point: dict[str, Any], *, own_key: str, interface_name: str) -> dict[str, str]:
    """Who received the frame behind one track point (aprs.fi-like point details)."""
    reception = frame_reception(str(point.get("line") or ""), source_kind=str(point.get("source_kind") or ""),
                                own_callsign=own_key)
    if reception is None:
        return {"received_by": interface_name} if interface_name else {}
    where = f" · {interface_name}" if interface_name else ""
    if reception.via == VIA_RF:
        received_by = f"{reception.receiver or t('My station')} ({t('my station, RF')}){where}"
    elif reception.via == VIA_IGATE:
        received_by = f"iGate {reception.receiver or '-'} ({reception.q_construct}){where}"
    else:
        received_by = f"{t('APRS-IS server')} {reception.receiver or '-'} ({reception.q_construct}, {t('not heard on RF')}){where}"
    hops = [hop or t("unknown digipeater") for hop in reception.hops]
    if hops:
        digipeaters = " → ".join(hops)
    else:
        digipeaters = t("none, sent over the Internet") if reception.via == VIA_INTERNET else t("direct, no digipeater")
    parts = {
        "received_by": received_by,
        "digipeaters": digipeaters,
        "path": reception.path or "-",
        "via": reception.via,
    }
    if reception.gateway:
        parts["gateway"] = reception.gateway
    return parts


def track_point_tooltip(callsign: str, point: dict[str, Any], *, own_key: str, interface_name: str,
                        pick_hint: bool = False) -> str:
    rows = [f"<b style='font-size:11pt'>{escape_html(callsign)}</b>"]

    def add(label: str, value: Any) -> None:
        if value not in (None, ""):
            rows.append(f"<b>{escape_html(label)}:</b> {escape_html(value)}")

    add(t("Time"), format_display_datetime(point.get("heard_at")) if point.get("heard_at") else "")
    parts = point_reception_parts(point, own_key=own_key, interface_name=interface_name)
    add(t("Received by"), parts.get("received_by"))
    add(t("Sent from APRS-IS to RF by"), parts.get("gateway"))
    add(t("Digipeaters"), parts.get("digipeaters"))
    add(t("Path"), parts.get("path"))
    if pick_hint and parts.get("via") in (VIA_RF, VIA_IGATE):
        rows.append(pick_hint_row())
    return "<div style='white-space:nowrap'>" + "<br>".join(rows) + "</div>"


def track_shapes(tracks: list[dict[str, Any]], *, own_key: str = "",
                 interface_names: dict[Any, str] | None = None, pick_hint: bool = False) -> list[MapPolyline]:
    """Mobile tracks; ``pick_hint`` when the map draws reception lines on a right click (TrackReceptionLines)."""
    shapes: list[MapPolyline] = []
    halo = overlay_contrast_color()
    names = interface_names or {}
    for track in tracks:
        callsign = str(track.get("display_callsign") or "")
        points: list[tuple[float, float]] = []
        sources: list[dict[str, Any]] = []
        for point in track.get("points") or []:
            latitude = parse_float(point.get("latitude"))
            longitude = parse_float(point.get("longitude"))
            if latitude is None or longitude is None:
                continue
            if points and abs(points[-1][0] - latitude) < 1e-6 and abs(points[-1][1] - longitude) < 1e-6:
                continue
            points.append((latitude, longitude))
            sources.append(point)
        points = points[-MOBILE_TRACK_MAX_RENDERED_POINTS:]
        sources = sources[-MOBILE_TRACK_MAX_RENDERED_POINTS:]
        if len(points) < 2:
            continue
        color = callsign_color(callsign)
        color.setAlphaF(0.96)
        payloads = [
            {**point, "display_callsign": callsign, "interface_name": str(names.get(point.get("interface_id")) or "")}
            for point in sources
        ]
        shapes.append(
            MapPolyline(
                points=points,
                color=color,
                width=1.75,
                halo=halo,
                halo_width=3.0,
                dots=True,
                dot_radius=4.0,
                point_tooltips=[
                    track_point_tooltip(callsign, payload, own_key=own_key, interface_name=payload["interface_name"],
                                        pick_hint=pick_hint)
                    for payload in payloads
                ],
                point_payloads=payloads,
            )
        )
    return shapes


def alert_area_shapes(feature_collection: dict[str, Any] | None, hidden_alert_ids: set[int] | None = None,
                      *, fill_opacity: float = 0.10, width: float = 2.0) -> list[MapPolygon]:
    hidden_alert_ids = hidden_alert_ids or set()
    shapes: list[MapPolygon] = []
    for rings, properties in geojson_polygons(feature_collection):
        contributors = properties.get("aprsbox_alerts")
        color_name = str(properties.get("aprsbox_alert_color") or "gray")
        if isinstance(contributors, list):
            visible = [item for item in contributors if isinstance(item, dict) and _int(item.get("id")) not in hidden_alert_ids]
            if not visible:
                continue
            strongest = 0
            for item in visible:
                level = _int(item.get("severity_level"))
                if level in (1, 2, 3):
                    strongest = max(strongest, level)
            color_name = {1: "yellow", 2: "orange", 3: "red"}.get(strongest, "gray")
        color = alert_area_color(color_name)
        shapes.append(MapPolygon(rings=rings, stroke=color, fill=color, width=width, fill_opacity=fill_opacity))
    return shapes


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def path_segment_color(kind: str) -> QColor:
    colors = current_colors()
    if kind in (KIND_APRSIS, KIND_INTERNET):
        return colors.color("traffic-color-aprsis-rx", "#53b9c8")
    if kind == KIND_RF_DIRECT:
        return colors.color("success", "#8bcf9b")
    return colors.color("warning", "#d9b565")


def path_kind_label(kind: str) -> str:
    return {
        KIND_RF_DIRECT: t("Heard directly by my station"),
        KIND_RF_DIGI: t("RF hop via digipeater"),
        KIND_APRSIS: t("Heard by IGate (APRS-IS)"),
        KIND_INTERNET: t("Over the Internet (APRS-IS) to the gateway that sent it on RF"),
    }.get(kind, kind)


def path_shapes(segments: list[PathSegment]) -> list[MapPolyline]:
    shapes: list[MapPolyline] = []
    halo = overlay_contrast_color()
    halo.setAlphaF(0.30 if current_colors().is_dark else 0.45)
    for segment in segments:
        color = path_segment_color(segment.kind)
        stations = ", ".join(segment.stations[:12]) + (f" (+{len(segment.stations) - 12})" if len(segment.stations) > 12 else "")
        heard = format_display_datetime(segment.heard_at) if segment.heard_at else ""
        paths = "<br>".join(escape_html(text) for text in segment.paths[:6])
        if len(segment.paths) > 6:
            paths += f"<br>+{len(segment.paths) - 6}"
        tooltip = (
            f"<b>{escape_html(segment.start)} → {escape_html(segment.end)}</b><br>"
            f"{escape_html(path_kind_label(segment.kind))}"
            + (f"<br><i>{escape_html(t('Hop through an unknown digipeater (no callsign or position)'))}</i>" if segment.uncertain else "")
            + f"<br><b>{escape_html(t('Stations'))}:</b> {escape_html(stations)}"
            + (f"<br><b>{escape_html(t('Last heard'))}:</b> {escape_html(heard)}" if heard else "")
            + (f"<br><span style='font-family:Consolas'>{paths}</span>" if paths else "")
        )
        width = 2.2 if segment.kind == KIND_RF_DIRECT else 1.8
        shapes.append(
            MapPolyline(
                points=[segment.start_pos, segment.end_pos],
                color=color,
                width=width,
                halo=halo,
                halo_width=width + 2.2,
                dashed=segment.uncertain,
                tooltip=tooltip,
                payload=segment,
                opacity=0.92,
            )
        )
    return shapes


def reception_shapes(segments: list[PathSegment], *, fade: float = 1.0) -> list[MapPolyline]:
    """Lines of the way right-clicked track point frames took to whoever received them (``fade`` 1 -> 0 dims them)."""
    shapes: list[MapPolyline] = []
    halo = overlay_contrast_color()
    halo.setAlphaF(0.85 * fade)
    for segment in segments:
        rows = [f"<b>{escape_html(segment.start)} → {escape_html(segment.end)}</b>", escape_html(path_kind_label(segment.kind))]
        if segment.uncertain:
            rows.append(f"<i>{escape_html(t('Hop through an unknown digipeater (no callsign or position)'))}</i>")
        if segment.heard_at:
            rows.append(f"<b>{escape_html(t('Time'))}:</b> {escape_html(format_display_datetime(segment.heard_at))}")
        rows.extend(f"<span style='font-family:Consolas'>{escape_html(text)}</span>" for text in segment.paths[:1])
        width = 3.0 if segment.kind == KIND_RF_DIRECT else 2.6
        shapes.append(
            MapPolyline(
                points=[segment.start_pos, segment.end_pos],
                color=path_segment_color(segment.kind),
                width=width,
                halo=halo,
                halo_width=width + 3.0,
                dashed=segment.uncertain or segment.kind == KIND_INTERNET,
                tooltip="<br>".join(rows),
                payload=segment,
                opacity=fade,
                arrows=True,
            )
        )
    return shapes


RECEPTION_LINES_SECONDS = 10.0  # the user: the lines go out by themselves after 10 s ...
RECEPTION_FADE_SECONDS = 1.5    # ... dimming during the last part of it
RECEPTION_FADE_STEP_MS = 50


class TrackReceptionLines:
    """Right click on track dots draws the way each frame took to whoever received it (like aprs.fi).

    The lines go from the dot through the digipeaters to the own station (RF) or to the
    iGate (qAR/qAO).  They dim and go out by themselves after RECEPTION_LINES_SECONDS,
    or earlier when the same dots are right-clicked again, the map is right-clicked
    elsewhere or Esc is pressed.
    """

    def __init__(self, view: MapView, *, positions: Callable[[], dict[str, tuple[float, float]]],
                 own_key: Callable[[], str]) -> None:
        self.view = view
        self._positions = positions
        self._own_key = own_key
        self.payloads: list[dict[str, Any]] = []
        self._segments: list[PathSegment] = []
        self._shown_at = 0.0
        self._timer = QTimer(view)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        view.track_points_selected.connect(self.select)
        view.marker_right_clicked.connect(self._select_station)

    @staticmethod
    def _identity(payloads: list[dict[str, Any]]) -> list[Any]:
        return [(payload.get("frame_id"), payload.get("latitude"), payload.get("longitude")) for payload in payloads]

    def _select_station(self, station: Any, global_pos: QPoint | None = None) -> list[str]:
        """Right click on a station: the way of its latest frame."""
        latest = station_reception_payload(station)
        return self.select([latest] if latest is not None else [], global_pos)

    def select(self, payloads: list[Any], global_pos: QPoint | None = None) -> list[str]:
        """Draw the lines of these dots; returns what could not be drawn (also shown as a tooltip)."""
        chosen = [payload for payload in payloads or [] if isinstance(payload, dict)]
        if not chosen or self._identity(chosen) == self._identity(self.payloads):
            self.clear()
            return []
        self.payloads = chosen
        self._shown_at = time.monotonic()
        notes = self.refresh()
        if self._segments:
            self._timer.start(round((RECEPTION_LINES_SECONDS - RECEPTION_FADE_SECONDS) * 1000))
        else:
            self.payloads = []  # nothing drawn: the next right click explains it again
        if notes and global_pos is not None and not global_pos.isNull():
            QToolTip.showText(global_pos, "<br>".join(escape_html(note) for note in notes), self.view)
        return notes

    def _fade(self) -> float:
        remaining = RECEPTION_LINES_SECONDS - (time.monotonic() - self._shown_at)
        return max(0.0, min(1.0, remaining / RECEPTION_FADE_SECONDS))

    def _tick(self) -> None:
        fade = self._fade()
        if fade <= 0:
            self.clear()
            return
        self.view.set_layer("reception", reception_shapes(self._segments, fade=fade))
        self._timer.start(RECEPTION_FADE_STEP_MS)

    def refresh(self) -> list[str]:
        """Redraw for the current positions; returns what could not be drawn."""
        if not self.payloads:
            self._segments = []
            self.view.set_layer("reception", [])
            return []
        positions = self._positions()
        own_key = self._own_key()
        segments: list[PathSegment] = []
        notes: list[str] = []
        for payload in self.payloads:
            latitude = parse_float(payload.get("latitude"))
            longitude = parse_float(payload.get("longitude"))
            if latitude is None or longitude is None:
                continue
            station = str(payload.get("display_callsign") or "")
            start: tuple[float, float] | None = (latitude, longitude)
            sender = str(payload.get("sender") or "")
            if sender and sender != station.upper():  # an object or item: its owner put it on the air
                station, start = sender, positions.get(sender)
                if start is None:
                    notes.append(t("Position unknown on the map: {callsigns}").format(callsigns=sender))
                    continue
            found, reception, missing = reception_segments(
                str(payload.get("line") or ""),
                source_kind=str(payload.get("source_kind") or ""),
                own_callsign=own_key,
                station=station,
                start=start,
                positions=positions,
                heard_at=str(payload.get("heard_at") or ""),
            )
            segments.extend(found)
            if reception is None:
                notes.append(t("No frame details for this point"))
            elif reception.via == VIA_INTERNET:
                detail = f"{reception.q_construct}, {reception.receiver}" if reception.receiver else reception.q_construct
                notes.append(t("Received over the Internet ({detail}): nobody heard it on RF").format(detail=detail))
            if missing:
                notes.append(t("Position unknown on the map: {callsigns}").format(callsigns=", ".join(missing)))
        self._segments = segments
        self.view.set_layer("reception", reception_shapes(segments, fade=self._fade()))
        return list(dict.fromkeys(notes))

    def clear(self) -> None:
        self._timer.stop()
        self.payloads = []
        self._segments = []
        self.view.set_layer("reception", [])


def station_positions(stations: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for station in stations:
        latitude = parse_float(station.get("latitude"))
        longitude = parse_float(station.get("longitude"))
        if latitude is None or longitude is None:
            continue
        for key in (station.get("display_callsign"), station.get("callsign")):
            text = str(key or "").strip().upper()
            if text:
                positions.setdefault(text, (latitude, longitude))
                if text.endswith("-0"):
                    positions.setdefault(text[:-2], (latitude, longitude))
    return positions


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
