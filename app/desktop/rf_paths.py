"""RF path lines ("who hears whom through which digipeater") for the map.

For every recently received frame the used part of the AX.25 path is turned
into a chain of hops::

    source -> digi 1 -> digi 2 -> ... -> receiver

The receiver is the own station for frames received over RF, or the IGate
named after ``qAR``/``qAO``/``qAo`` for frames received from APRS-IS.  Path
tokens are interpreted exactly like the web map's traffic scroller
(``tokenizeUsedPath`` / ``isGenericAliasToken`` in map-scroller-overlay.js):

* when only one token carries ``*`` every token up to it was used,
* otherwise only the starred tokens were used,
* generic aliases (WIDEn-N, TRACEn-N, RELAY, SPn-N style region aliases, ...)
  are hops of an unknown digipeater unless they directly follow the callsign
  the digipeater inserted (``SR9XSR*,WIDE1*`` is a single hop).

Hops whose station position is unknown are skipped; the segment that bridges
them is flagged ``uncertain`` (drawn dashed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# Map time windows (seconds) as the user chose them: 1/5/10/15/30 s, 1 min, 5/15/30 min, 1/2/6 h.
RF_PATH_WINDOW_OPTIONS_SECONDS: tuple[int, ...] = (1, 5, 10, 15, 30, 60, 300, 900, 1800, 3600, 7200, 21600)
DEFAULT_RF_PATH_WINDOW_SECONDS = 3600
RF_PATH_FRAME_SCAN_LIMIT = 20000

KIND_RF_DIRECT = "rf_direct"
KIND_RF_DIGI = "rf_digi"
KIND_APRSIS = "aprsis"
KIND_INTERNET = "internet"  # third-party frame: from the station over APRS-IS to the IS -> RF gateway

_TNC2_RE = re.compile(r"^(?P<source>[^>]+?)\s*>\s*(?P<destination>[^,:]+?)(?:\s*,\s*(?P<path>[^:]+))?\s*:(?P<info>.*)$", re.S)
_GENERIC_ALIAS_RE = re.compile(r"^(?:WIDE|TRACE|RELAY|GATE)(?:\d+)?(?:-\d+)?$")
_REGION_ALIAS_RE = re.compile(r"^[A-Z]{1,5}\d(?:-\d+)?$")
_Q_CONSTRUCT_RE = re.compile(r"^q[A-Z]{2}$", re.IGNORECASE)
_RF_GATED_Q = {"QAR", "QAO"}
_SPECIAL_TOKENS = {"TCPIP", "TCPXX", "RFONLY", "NOGATE", "NORF"}


def normalize_callsign(value: Any) -> str:
    return str(value or "").strip().upper()


def base_callsign(value: Any) -> str:
    normalized = normalize_callsign(value)
    head, sep, ssid = normalized.partition("-")
    if sep and re.fullmatch(r"[A-Z0-9]{1,2}", ssid):
        return head
    return normalized


def strip_zero_ssid(value: Any) -> str:
    normalized = normalize_callsign(value)
    if normalized.endswith("-0"):
        return normalized[:-2]
    return normalized


def is_q_construct(token: str) -> bool:
    return bool(_Q_CONSTRUCT_RE.fullmatch(str(token or "").strip()))


def is_generic_alias(token: str) -> bool:
    normalized = normalize_callsign(token).rstrip("*")
    if not normalized:
        return False
    if is_q_construct(normalized):
        return True
    if _GENERIC_ALIAS_RE.fullmatch(normalized):
        return True
    if _REGION_ALIAS_RE.fullmatch(normalized):
        return True
    return normalized in _SPECIAL_TOKENS


def split_tnc2(line: str) -> dict[str, str] | None:
    match = _TNC2_RE.match(str(line or "").strip())
    if not match:
        return None
    return {
        "source": match.group("source").strip(),
        "destination": match.group("destination").strip(),
        "path": (match.group("path") or "").strip(),
        "info": match.group("info") or "",
    }


def path_tokens(path: str) -> list[str]:
    return [token.strip() for token in str(path or "").split(",") if token.strip()]


def used_path_tokens(tokens: list[str]) -> list[str]:
    """Port of map-scroller-overlay.js ``tokenizeUsedPath`` (q-constructs excluded)."""
    starred = [index for index, token in enumerate(tokens) if token.endswith("*")]
    if not starred:
        return []
    if len(starred) == 1:
        used = [token.rstrip("*").strip() for token in tokens[: starred[0] + 1]]
    else:
        used = [token.rstrip("*").strip() for token in tokens if token.endswith("*")]
    return [token for token in used if token and not is_q_construct(token)]


@dataclass(slots=True)
class FrameRoute:
    station: str
    hops: list[str | None]  # digipeater callsigns, None = unknown digipeater
    receiver: str
    kind: str  # KIND_RF_DIRECT / KIND_RF_DIGI / KIND_APRSIS
    heard_at: str
    path: str
    interface_id: int | None = None


def path_hops(rf_tokens: list[str], station: str) -> list[str | None]:
    """Digipeaters that repeated a frame, in order (None = a generic alias used by an unknown digi)."""
    hops: list[str | None] = []
    previous_was_callsign = False
    for token in used_path_tokens(rf_tokens):
        if normalize_callsign(token).rstrip("*") in _SPECIAL_TOKENS:
            continue  # TCPIP*, NOGATE ... mark how a frame travelled, no digipeater repeated it
        if is_generic_alias(token):
            if not previous_was_callsign:
                hops.append(None)
            previous_was_callsign = False
            continue
        hops.append(strip_zero_ssid(token))
        previous_was_callsign = True
    # A station never hears itself through its own path entry.
    return [hop for hop in hops if hop is None or hop != station]


@dataclass(slots=True)
class FrameReception:
    """Who received one frame (map track point details, like aprs.fi)."""

    receiver: str  # own station (RF) or the iGate / APRS-IS server after the q-construct
    via: str  # VIA_RF, VIA_IGATE (heard on RF by an iGate) or VIA_INTERNET (qAC, qAS ... no RF)
    q_construct: str
    hops: list[str | None]
    path: str
    gateway: str = ""  # third-party frame: the station that sent it from APRS-IS to RF


VIA_RF = "rf"
VIA_IGATE = "igate"
VIA_INTERNET = "internet"


def frame_reception(line: str, *, source_kind: str, own_callsign: str) -> FrameReception | None:
    parsed = split_tnc2(line)
    if parsed is None:
        return None
    gateway = ""
    info = parsed["info"]
    if info.startswith("}") and str(source_kind or "").strip().lower() != "aprsis":
        gateway = strip_zero_ssid(parsed["source"])  # IS -> RF: we heard the gateway's transmission
    tokens = path_tokens(parsed["path"])
    q_index = next((index for index, token in enumerate(tokens) if is_q_construct(token)), None)
    if q_index is None:
        hops = path_hops(tokens, strip_zero_ssid(parsed["source"]))
        return FrameReception(strip_zero_ssid(own_callsign), VIA_RF, "", hops, parsed["path"], gateway)
    q_construct = tokens[q_index]
    receiver = strip_zero_ssid(tokens[q_index + 1].rstrip("*")) if q_index + 1 < len(tokens) else ""
    via = VIA_IGATE if q_construct.upper() in _RF_GATED_Q else VIA_INTERNET
    hops = path_hops(tokens[:q_index], strip_zero_ssid(parsed["source"]))
    if hops and hops[-1] is not None and hops[-1] == receiver:
        hops = hops[:-1]
    return FrameReception(receiver, via, q_construct, hops, parsed["path"], gateway)


def object_owner(line: str) -> str:
    """The station that put an object or item frame on the air ('' for any other frame)."""
    parsed = split_tnc2(line)
    if parsed is None:
        return ""
    if parsed["info"].startswith("}"):  # third-party: the object travels inside
        parsed = split_tnc2(parsed["info"][1:])
        if parsed is None:
            return ""
    return strip_zero_ssid(parsed["source"]) if parsed["info"][:1] in (";", ")") else ""


def reception_segments(line: str, *, source_kind: str, own_callsign: str, station: str,
                       start: tuple[float, float], positions: dict[str, tuple[float, float]],
                       heard_at: str = "") -> tuple[list[PathSegment], FrameReception | None, list[str]]:
    """The way one frame took from where it was sent (a track point) to whoever received it.

    Returns the segments to draw, the reception and the stations of that way whose
    position is unknown.  Unknown digipeaters are skipped like in ``build_segments``
    (the bridging segment is ``uncertain``); a frame that only travelled over the
    Internet has no segments.
    """
    reception = frame_reception(line, source_kind=source_kind, own_callsign=own_callsign)
    if reception is None or reception.via == VIA_INTERNET or not reception.receiver:
        return [], reception, []
    via_igate = reception.via == VIA_IGATE
    nodes: list[tuple[str | None, str]] = []  # (station, kind of the segment that ends there)
    if reception.gateway:
        nodes.append((reception.gateway, KIND_INTERNET))
    nodes.extend((hop, KIND_APRSIS if via_igate else KIND_RF_DIGI) for hop in reception.hops)
    nodes.append((reception.receiver, KIND_APRSIS if via_igate else KIND_RF_DIRECT))
    header = str(line or "").split(":", 1)[0]
    segments: list[PathSegment] = []
    missing: list[str] = []
    current, current_pos, gap = station, start, False
    for index, (node, kind) in enumerate(nodes):
        position = positions.get(node) if node is not None else None
        if position is None:
            if node is not None:
                missing.append(node)
            if kind == KIND_INTERNET:
                return [], reception, missing  # unknown gateway: no telling where the RF part began
            gap = True
            continue
        if position != current_pos:
            segments.append(PathSegment(start=current, end=node, start_pos=current_pos, end_pos=position, kind=kind,
                                        uncertain=gap, hop_index=index, heard_at=str(heard_at or ""),
                                        stations=[station], paths=[header]))
        current, current_pos, gap = node, position, False
    return segments, reception, missing


def route_from_frame(line: str, *, source_kind: str, own_callsign: str, heard_at: str = "",
                     interface_id: int | None = None) -> FrameRoute | None:
    parsed = split_tnc2(line)
    if parsed is None:
        return None
    if parsed["info"].startswith("}"):
        return None  # third-party traffic re-transmitted from the Internet
    station = strip_zero_ssid(parsed["source"])
    if not station:
        return None
    tokens = path_tokens(parsed["path"])
    is_aprsis = str(source_kind or "").strip().lower() == "aprsis"
    receiver = ""
    rf_tokens = tokens
    if is_aprsis:
        q_index = next((index for index, token in enumerate(tokens) if is_q_construct(token)), None)
        if q_index is None:
            return None
        q_type = tokens[q_index].upper()
        if q_type not in _RF_GATED_Q:
            return None  # not heard on RF by an IGate (qAC, qAS, qAX, qAU, qAI ...)
        if q_index + 1 >= len(tokens):
            return None
        receiver = strip_zero_ssid(tokens[q_index + 1].rstrip("*"))
        rf_tokens = tokens[:q_index]
        if not receiver or receiver.startswith("T2") or receiver in {"TCPIP", "TCPXX"}:
            return None
    else:
        receiver = strip_zero_ssid(own_callsign)
        if not receiver:
            return None
    hops = path_hops(rf_tokens, station)
    if hops and hops[-1] is not None and hops[-1] == receiver:
        hops = hops[:-1]
    if receiver == station:
        return None
    kind = KIND_APRSIS if is_aprsis else (KIND_RF_DIGI if hops else KIND_RF_DIRECT)
    return FrameRoute(
        station=station,
        hops=hops,
        receiver=receiver,
        kind=kind,
        heard_at=str(heard_at or ""),
        path=parsed["path"],
        interface_id=interface_id,
    )


@dataclass(slots=True)
class PathSegment:
    start: str
    end: str
    start_pos: tuple[float, float]
    end_pos: tuple[float, float]
    kind: str
    uncertain: bool
    hop_index: int  # 0 = the hop leaving the originating station
    heard_at: str
    stations: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


def build_segments(routes: Iterable[FrameRoute], positions: dict[str, tuple[float, float]], *,
                   include_rf: bool = True, include_aprsis: bool = True,
                   visible_interface_ids: set[int] | None = None,
                   known_interface_ids: set[int] | None = None) -> list[PathSegment]:
    """Latest route per (station, receiver kind) -> de-duplicated map segments."""
    latest: dict[tuple[str, str], FrameRoute] = {}
    for route in routes:
        if route.kind == KIND_APRSIS and not include_aprsis:
            continue
        if route.kind != KIND_APRSIS and not include_rf:
            continue
        if (
            visible_interface_ids is not None
            and route.interface_id is not None
            and known_interface_ids is not None
            and route.interface_id in known_interface_ids
            and route.interface_id not in visible_interface_ids
        ):
            continue
        group = "aprsis" if route.kind == KIND_APRSIS else "rf"
        key = (route.station, group)
        current = latest.get(key)
        if current is None or route.heard_at >= current.heard_at:
            latest[key] = route

    segments: dict[tuple[str, str, str], PathSegment] = {}
    for route in latest.values():
        if positions.get(route.station) is None or positions.get(route.receiver) is None:
            continue
        nodes: list[str | None] = [route.station, *route.hops, route.receiver]
        resolved: list[tuple[str, tuple[float, float], int]] = []
        gaps_before: list[bool] = []
        gap = False
        for index, node in enumerate(nodes):
            position = positions.get(node) if node is not None else None
            if position is None:
                gap = True
                continue
            resolved.append((node, position, index))
            gaps_before.append(gap)
            gap = False
        for position_index in range(1, len(resolved)):
            start_node, start_pos, start_index = resolved[position_index - 1]
            end_node, end_pos, _end_index = resolved[position_index]
            if start_node == end_node:
                continue
            uncertain = gaps_before[position_index]
            if route.kind == KIND_APRSIS:
                kind = KIND_APRSIS
            elif end_node == route.receiver:
                kind = KIND_RF_DIRECT  # last hop: the own station hears start_node directly
            else:
                kind = KIND_RF_DIGI
            key = (start_node, end_node, kind)
            segment = segments.get(key)
            if segment is None:
                segment = PathSegment(
                    start=start_node,
                    end=end_node,
                    start_pos=start_pos,
                    end_pos=end_pos,
                    kind=kind,
                    uncertain=uncertain,
                    hop_index=start_index,
                    heard_at=route.heard_at,
                )
                segments[key] = segment
            else:
                segment.uncertain = segment.uncertain and uncertain
                if route.heard_at > segment.heard_at:
                    segment.heard_at = route.heard_at
            if route.station not in segment.stations:
                segment.stations.append(route.station)
            route_text = f"{route.station}>{route.path}" if route.path else route.station
            if route_text not in segment.paths:
                segment.paths.append(route_text)
    order = {KIND_APRSIS: 0, KIND_RF_DIGI: 1, KIND_RF_DIRECT: 2}
    return sorted(segments.values(), key=lambda item: (order.get(item.kind, 9), item.heard_at))


def load_recent_routes(window_seconds: int, own_callsign: str, *, now: datetime | None = None,
                       limit: int = RF_PATH_FRAME_SCAN_LIMIT) -> list[FrameRoute]:
    from app.db import fetch_all

    reference = now or datetime.now(timezone.utc)
    cutoff = (reference - timedelta(seconds=max(1, int(window_seconds)))).replace(microsecond=0).isoformat()
    rows = fetch_all(
        """
        SELECT line, source_kind, interface_id, created_at
        FROM traffic_frames
        WHERE format = 'TNC2'
          AND UPPER(COALESCE(direction, 'RX')) = 'RX'
          AND created_at >= ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (cutoff, int(limit)),
    )
    routes: list[FrameRoute] = []
    for row in rows:
        interface_id = row["interface_id"]
        try:
            interface_value = int(interface_id) if interface_id is not None else None
        except (TypeError, ValueError):
            interface_value = None
        route = route_from_frame(
            str(row["line"] or ""),
            source_kind=str(row["source_kind"] or "rf"),
            own_callsign=own_callsign,
            heard_at=str(row["created_at"] or ""),
            interface_id=interface_value,
        )
        if route is not None:
            routes.append(route)
    return routes


def own_station_identity(station_settings: dict[str, Any]) -> str:
    callsign = normalize_callsign(station_settings.get("callsign"))
    ssid = str(station_settings.get("ssid") or "").strip()
    if callsign and ssid and ssid != "0" and ssid.isdigit():
        return f"{callsign}-{ssid}"
    return callsign
