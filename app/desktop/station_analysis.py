"""Traffic analysis of one callsign for the station card ("Traffic analyzer"). Runs on a worker thread.

The analysis rules are SP9DLM's (APRS ANALIZER, analyze.py, 2026-11-17), used with his consent; here they read
APRSBox's own data instead of an aprx log:

* RF frames of every RF interface (2 m, LoRa) - the traffic table for the retention, the daily traffic logs
  (logs/traffic/<interface>/<date>.log) before it, so 24-96 h work with a 6 h retention;
* APRS-IS frames - the traffic table only (they are not in the daily logs);
* positions - the map state, the station settings and the frames themselves;
* optionally findu.com (archive of the whole APRS-IS): positions of the last 24 h and messages.

Verdicts are English templates with parameters; the desktop translates them (pl.json).
"""

from __future__ import annotations

import collections
import html
import math
import re
import statistics
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PERIOD_HOURS = (1, 6, 24, 48, 72, 96)
NEAR_INFRA_KM = 50  # "dense area": gateways/digipeaters within this radius
REGIONAL_DIGIS = {"SP9DLM": "regional digipeater"}  # wide coverage, WIDE2-2 intended (SP9DLM, 2026-11-17)
DENSE_MIN = 3  # from this many gateways/digipeaters around, 2 hops are too many
DUP_SEC = 60  # the same content within this window = a copy (digi echo), not a new transmission
FINDU_TIMEOUT_S = 12
FINDU_POSITION_LOOKUPS = 6
CALL_RE = re.compile(r"^[A-Z0-9]{1,6}(-[A-Z0-9]{1,2})?$")
ALIAS_RE = re.compile(r"^(WIDE|TRACE|RELAY|ECHO|GATE|NI|NF|KB|SP|HOP|TCPIP|TCPXX|NOGATE|RFONLY)\d?(-\d{1,2})?$")
WIDE_RE = re.compile(r"^(WIDE|TRACE)([1-7])-([0-7])$")
LOG_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (TX-REMOTE|RX|TX)\s+(.*)$")
APRSIS = "APRS-IS"


def norm_call(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if CALL_RE.match(text) else None


def kind_of(info: str) -> str:
    """Frame kind from the first character of the information field."""
    if not info:
        return "other"
    first = info[0]
    if first == "}":
        return "thirdparty"
    end = info.find(":", 1) if first == ":" else -1
    if 2 <= end <= 14:  # addressee (9 characters by the spec, sometimes shorter or longer)
        to, body = info[1:end].strip(), info[end + 1:]
        if body.startswith(("PARM.", "UNIT.", "EQNS.", "BITS.")):
            return "telem"
        if re.match(r"^(ack|rej)[A-Za-z0-9]{1,5}(}[A-Za-z0-9]{0,5})?\s*$", body):
            return "ack"
        if to.startswith(("BLN", "NWS")):
            return "bulletin"
        return "msg"
    if info.startswith("T#"):
        return "telem"
    if first == ">":
        return "status"
    if first in ";)":
        return "object"
    if first in "!=/@`'\x1c\x1d$_":
        return "beacon"
    return "other"


def split_frame(frame: str) -> tuple[str, str, list[str], str] | None:
    """'SRC>DEST,h1,h2:info' -> (src, dest, [hops], info) or None."""
    if ">" not in frame or ":" not in frame:
        return None
    header, info = frame.split(":", 1)
    source, rest = header.split(">", 1)
    parts = rest.split(",")
    return source.strip(), parts[0].strip(), [hop.strip() for hop in parts[1:] if hop.strip()], info


def rf_path(hops: list[str]) -> tuple[list[str], str, str]:
    """The path before the q construct (APRS-IS frames), the q construct and the iGate."""
    for index, hop in enumerate(hops):
        if hop.startswith("qA"):
            return hops[:index], hop, (hops[index + 1] if index + 1 < len(hops) else "")
    return hops, "", ""


def used_hops(hops: list[str]) -> list[str]:
    last = max((index for index, hop in enumerate(hops) if hop.endswith("*")), default=-1)
    return [hop.rstrip("*") for hop in hops[:last + 1]]


def transmitter(hops: list[str]) -> str | None:
    """Who sent this copy: the last callsign (not an alias) of the used part of the path."""
    for hop in reversed(used_hops(hops)):
        if not ALIAS_RE.match(hop):
            return hop
    return None


def path_hops(path: list[str]) -> tuple[int, list[str]]:
    """How many hops the path asks for (sum of N of WIDEn-N) and the outdated aliases in it."""
    count, old = 0, []
    for hop in path:
        match = WIDE_RE.match(hop)
        if match:
            count += int(match.group(3))
        elif re.match(r"^(RELAY|TRACE\d?|WIDE)(-\d)?$", hop):
            old.append(hop)
            count += 1
    return count, old


def msg_to(info: str) -> str:
    return info[1:info.find(":", 1)].strip()


def msg_text(info: str) -> str:
    return info[info.find(":", 1) + 1:].split("{", 1)[0].strip()


def dist_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat()


def _ts(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


# ------------------------------------------------------------------------------------------ frames
def _rf_interfaces() -> dict[int, dict[str, str]]:
    from app.db import fetch_all

    return {int(row["id"]): {"name": str(row["name"] or ""), "band": str(row["band"] or "")}
            for row in fetch_all("SELECT id, name, band FROM modems WHERE modem_type <> 'APRSIS'")}


def collect_frames(call: str, *, since: float, now: float) -> dict[str, Any]:
    """Frames that mention ``call`` (as sender, in the path or as iGate) since ``since``.

    Rows: (ts, interface name or APRS-IS, "R"/"T", TNC2 line). The traffic table gives the retention, the
    daily logs the RF frames before it.
    """
    from app.db import fetch_all, traffic_retention_cutoff
    from app.desktop.traffic_logs import interface_log_dir

    base = call.split("-", 1)[0]
    retention_from = _ts(traffic_retention_cutoff()) or now
    interfaces = _rf_interfaces()
    rows: list[tuple[float, str, str, str]] = []
    for row in fetch_all(
        """
        SELECT created_at, source_kind, interface_id, direction, line
        FROM traffic_frames
        WHERE created_at >= ? AND line LIKE ?
        ORDER BY id
        """,
        (_iso(max(since, retention_from)), f"%{base}%"),
    ):
        ts = _ts(row["created_at"])
        if ts is None or ts < since:
            continue
        direction = "T" if str(row["direction"] or "") == "tx" else "R"
        if str(row["source_kind"] or "") == "aprsis":
            if direction == "R":
                rows.append((ts, APRSIS, "R", _tnc2(row["line"])))
            continue
        interface = interfaces.get(int(row["interface_id"])) if row["interface_id"] is not None else None
        if interface is None:
            continue
        rows.append((ts, interface["name"], direction, _tnc2(row["line"])))
    log_from = since
    if since < retention_from:
        for interface_id, interface in interfaces.items():
            folder = interface_log_dir(interface["name"], interface_id)
            day = datetime.fromtimestamp(since, timezone.utc).date()
            last_day = datetime.fromtimestamp(retention_from, timezone.utc).date()
            while day <= last_day:
                path = folder / f"{day.isoformat()}.log"
                rows.extend(_read_log(path, interface["name"], base, since, retention_from))
                day += timedelta(days=1)
    rows.sort(key=lambda item: item[0])
    return {"rows": rows, "retention_from": retention_from, "log_from": log_from}


def _tnc2(line: Any) -> str:
    from app.desktop.traffic_logs import tnc2

    return tnc2(str(line or "")).strip()


def _read_log(path: Path, interface: str, base: str, since: float, until: float) -> list[tuple[float, str, str, str]]:
    out: list[tuple[float, str, str, str]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if base not in line:
                    continue
                match = LOG_LINE_RE.match(line.rstrip("\r\n"))
                if not match:
                    continue
                ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                if since <= ts < until:
                    frame = re.sub(r"\s\[hex [0-9A-F ]+\]$", "", match.group(3))
                    out.append((ts, interface, "R" if match.group(2) == "RX" else "T", frame))
    except OSError:
        pass
    return out


# ------------------------------------------------------------------------------------------ findu
def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "APRSBox-analyzer/1.0"})
    with urllib.request.urlopen(request, timeout=FINDU_TIMEOUT_S) as response:  # noqa: S310 - fixed findu.com URLs
        return response.read().decode("utf-8", errors="replace")


def findu(call: str, now: float, window: float) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False}
    try:
        text = _fetch("http://www.findu.com/cgi-bin/rawposit.cgi?call=%s&time=%d" % (call, max(1, round(window / 3600))))
        positions = []
        for line in re.sub(r"<[^>]*>", "\n", text).splitlines():
            match = re.match(r"^\s*(\d{14}),(.+)$", line)
            if not match:
                continue
            ts = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            frame = split_frame(html.unescape(match.group(2)))
            if frame and frame[0].upper() == call and ts >= now - window:
                positions.append((ts, frame))
        igates: collections.Counter[str] = collections.Counter()
        tcp = 0
        raw = []
        for ts, (_source, _dest, hops, info) in positions:
            path, q, igate = rf_path(hops)
            if "TCPIP*" in hops or q in ("qAC", "qAS", "qAU", "qAI"):
                tcp += 1
            elif igate:
                igates[igate] += 1
                if q in ("qAR", "qAO", "qAo"):
                    raw.append((ts, path, igate, info))
        result.update({"ok": True, "n": len(positions), "tcpip": tcp, "igates": igates.most_common(12), "raw": raw,
                       "frames": positions})
        messages = []
        text = _fetch("http://www.findu.com/cgi-bin/msg.cgi?call=%s" % call)
        year = datetime.fromtimestamp(now, timezone.utc).year
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
            cells = [html.unescape(re.sub(r"<[^>]*>", "", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
            if len(cells) < 5 or cells[0] == "from":
                continue
            match = re.match(r"(\d\d)/(\d\d)\s+(\d\d):(\d\d):(\d\d)z", cells[2])
            if not match:
                continue
            moment = datetime(year, int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)),
                              int(match.group(5)), tzinfo=timezone.utc)
            if moment.timestamp() > now + 86400:
                moment = moment.replace(year=year - 1)
            body = cells[4].split("{", 1)[0]
            if body.startswith(("PARM.", "UNIT.", "EQNS.", "BITS.")):
                continue  # telemetry definitions sent "to itself"
            messages.append({"t": moment.timestamp(), "from": cells[0], "to": cells[1], "text": body})
        result["msgs"] = messages[:15]
    except Exception as exc:  # noqa: BLE001 - findu.com is an extra; the analysis goes on without it
        result["err"] = exc.__class__.__name__
    return result


# ---------------------------------------------------------------------------------------- positions
def _known_positions() -> dict[str, tuple[float, float]]:
    from app.desktop.map_layers import station_positions
    from app.services.content import get_station_settings
    from app.services.map_station_state import read_map_station_state

    station_settings = get_station_settings(include_tx_runtime=False)
    positions = {key.upper(): value for key, value in station_positions(read_map_station_state(station_settings=station_settings)["snapshots"]).items()}
    own = _own_identity(station_settings)
    try:
        if own:
            positions[own] = (float(station_settings["latitude"]), float(station_settings["longitude"]))
    except (KeyError, TypeError, ValueError):
        pass
    return positions


def _own_identity(station_settings: dict[str, Any] | None = None) -> str:
    from app.desktop.rf_paths import own_station_identity
    from app.services.content import get_station_settings

    return str(own_station_identity(station_settings or get_station_settings(include_tx_runtime=False)) or "").upper()


def _position_of_frame(frame: str) -> tuple[float, float] | None:
    from app.services.content import parse_tnc2_frame

    try:
        aprs = (parse_tnc2_frame(frame) or {}).get("aprs_data") or {}
        latitude, longitude = aprs.get("latitude"), aprs.get("longitude")
        if latitude is None or longitude is None:
            return None
        return float(latitude), float(longitude)
    except Exception:  # noqa: BLE001 - one odd frame is no position
        return None


def _findu_position(call: str) -> tuple[float, float] | None:
    text = _fetch("http://www.findu.com/cgi-bin/rawposit.cgi?call=%s&time=168" % call)
    found = None
    for line in re.sub(r"<[^>]*>", "\n", text).splitlines():
        match = re.match(r"^\s*(\d{14}),(.+)$", line)
        if match:
            found = _position_of_frame(html.unescape(match.group(2))) or found
    return found


# ----------------------------------------------------------------------------------------- analysis
def analyze_station(call: str, *, hours: int = 24, use_findu: bool = False) -> dict[str, Any]:
    call = norm_call(call) or ""
    if not call:
        return {"error": "invalid_call"}
    hours = hours if hours in PERIOD_HOURS else 24
    now = time.time()
    window = hours * 3600.0
    collected = collect_frames(call, since=now - window, now=now)
    own_station = _own_identity()
    interfaces = sorted({interface["name"] for interface in _rf_interfaces().values()})
    bands: dict[str, dict[str, list]] = {name: {"own": [], "rx": [], "digi": [], "is2rf": []} for name in interfaces}
    band_labels = {interface["name"]: interface["band"] for interface in _rf_interfaces().values()}
    aprsis_own: list[tuple] = []
    aprsis_igate: list[tuple] = []
    positions: list[tuple[float, float]] = []
    for ts, interface, direction, frame in collected["rows"]:
        parts = split_frame(frame)
        if not parts:
            continue
        source, dest, hops, info = parts
        if interface == APRSIS:
            path, q, igate = rf_path(hops)
            if source.upper() == call:
                aprsis_own.append((ts, dest, path, q, igate, info))
                position = _position_of_frame(frame)
                if position:
                    positions.append(position)
            if igate.upper() == call and q in ("qAR", "qAO", "qAo"):
                aprsis_igate.append((ts, source, info))
            continue
        band = bands.get(interface)
        if band is None:
            continue
        # This station's own transmissions: APRSBox writes the digipeated header with its callsign marked.
        sender_of_copy = own_station if direction == "T" else transmitter(hops)
        if "TCPIP" in ",".join(hops):
            # an internet frame put on RF by a gateway = the last used callsign
            if sender_of_copy and sender_of_copy.upper() == call:
                band["is2rf"].append((ts, source, info))
            continue
        if source.upper() == call:
            kind = kind_of(info)
            if kind == "thirdparty":
                inner = split_frame(info[1:])
                band["is2rf"].append((ts, inner[0] if inner else "?", inner[3] if inner else info))
                continue
            band["own"].append((ts, kind, dest, hops, info))
            if direction == "R":
                band["rx"].append((ts, interface, hops, info))
            position = _position_of_frame(frame)
            if position:
                positions.append(position)
        elif (direction == "T" and own_station == call) or call in [hop.upper() for hop in used_hops(hops)]:
            band["digi"].append((ts, source, hops, info, sender_of_copy))

    known = _known_positions()
    me = positions[-1] if positions else known.get(call)
    result: dict[str, Any] = {"call": call, "hours": hours, "now": now, "from": now - window,
                              "aprsis_from": max(now - window, collected["retention_from"]), "bands": {}}
    if me and own_station and own_station in known and call != own_station:
        result["dist_km"] = round(dist_km(known[own_station][0], known[own_station][1], me[0], me[1]), 1)
    if positions:
        spread = max(dist_km(positions[-1][0], positions[-1][1], p[0], p[1]) for p in positions)
        result["mobile"] = spread > 0.5
    verdicts: list[tuple[str, str, dict[str, Any]]] = []
    direct_paths: collections.Counter[str] = collections.Counter()
    path_last: dict[str, float] = {}
    any_digi = 0
    for name, band in bands.items():
        own, digi = band["own"], band["digi"]
        unique: list[dict[str, Any]] = []
        last_by_info: dict[str, dict[str, Any]] = {}
        for ts, kind, _dest, hops, info in own:
            previous = last_by_info.get(info)
            if previous is not None and ts - previous["t"] <= DUP_SEC:
                previous["copies"] += 1
                previous["via"].update(hop for hop in used_hops(hops) if not ALIAS_RE.match(hop))
            else:
                previous = {"t": ts, "k": kind, "info": info, "copies": 1, "path": ",".join(hops) or "—",
                            "via": set(hop for hop in used_hops(hops) if not ALIAS_RE.match(hop))}
                unique.append(previous)
                last_by_info[info] = previous
            if not used_hops(hops):
                key = ",".join(hops) or ""
                direct_paths[key] += 1
                path_last[key] = max(path_last.get(key, 0), ts)
        kinds = collections.Counter(item["k"] for item in unique)
        beacon_times = [item["t"] for item in unique if item["k"] == "beacon"]
        beacon_iv = statistics.median([b - a for a, b in zip(beacon_times, beacon_times[1:])]) if len(beacon_times) >= 3 else None
        telemetry_times = [item["t"] for item in unique if item["k"] == "telem" and item["info"].startswith("T#")]
        telemetry_iv = statistics.median([b - a for a, b in zip(telemetry_times, telemetry_times[1:])]) if len(telemetry_times) >= 3 else None
        via = collections.Counter(hop for item in unique for hop in item["via"])
        messages = [{"t": item["t"], "to": msg_to(item["info"]), "text": msg_text(item["info"])}
                    for item in unique if item["k"] in ("msg", "bulletin")]
        repeated, last_digi = [], {}
        dups = first_hop = next_hop = 0
        sources: collections.Counter[str] = collections.Counter()
        for ts, source, hops, info, sender in digi:
            key = (source, info)
            previous = last_digi.get(key)
            mine = bool(sender and sender.upper() == call)
            if previous is not None and ts - previous["t"] <= DUP_SEC:
                if mine:
                    previous["tx"] += 1
                    if previous["tx"] == 2:
                        dups += 1
                continue
            last_digi[key] = {"t": ts, "tx": 1 if mine else 0}
            repeated.append(ts)
            sources[source] += 1
            used = [hop.upper() for hop in used_hops(hops)]
            if not used or used[0] == call:
                first_hop += 1
            else:
                next_hop += 1
        any_digi += len(repeated)
        is2rf = band["is2rf"]
        is_messages = sum(1 for _ts, _src, info in is2rf if kind_of(info) in ("msg", "ack"))
        result["bands"][name] = {
            "band": band_labels.get(name, ""),
            "own_unique": len(unique), "own_heard": len(own), "kinds": dict(kinds),
            "beacon_iv_min": round(beacon_iv / 60, 1) if beacon_iv else None,
            "telem_iv_min": round(telemetry_iv / 60, 1) if telemetry_iv else None,
            "copies_avg": round(len(own) / len(unique), 1) if unique else None,
            "via": via.most_common(10), "msgs": messages[-10:],
            "digi_n": len(repeated), "digi_per_h": round(len(repeated) / float(hours), 1), "digi_first": first_hop,
            "digi_next": next_hop, "digi_dups": dups, "digi_sources": sources.most_common(10),
            "is2rf_n": len(is2rf), "is2rf_msg": is_messages, "is2rf_other": len(is2rf) - is_messages,
            "is2rf_ex": [{"t": ts, "src": src, "text": info[:80]} for ts, src, info in is2rf[-5:]],
            "last": [{"t": item["t"], "k": item["k"], "path": item["path"], "copies": item["copies"],
                      "via": sorted(item["via"]), "text": item["info"][:90]} for item in unique[-12:]][::-1],
        }

    # APRS-IS (the stream this station receives; the traffic table only)
    q_count: collections.Counter[str] = collections.Counter()
    tcp = 0
    for ts, _dest, path, q, igate, _info in aprsis_own:
        if q in ("qAC", "qAS", "qAU", "qAI") or any(hop.startswith("TCPIP") for hop in path):
            tcp += 1
        else:
            q_count[igate] += 1
            if not used_hops(path):
                key = ",".join(path) or ""
                direct_paths[key] += 1
                path_last[key] = max(path_last.get(key, 0), ts)
    gated_sources = collections.Counter(source for _ts, source, _info in aprsis_igate)
    result["aprsis"] = {"n": len(aprsis_own), "tcpip": tcp, "igates": q_count.most_common(10),
                        "kinds": dict(collections.Counter(kind_of(item[5]) for item in aprsis_own)),
                        "igate_n": len(aprsis_igate), "igate_stations": len(gated_sources),
                        "igate_sources": gated_sources.most_common(10)}
    found = findu(call, now, window) if use_findu else {"ok": False, "off": True}
    result["findu"] = {key: value for key, value in found.items() if key not in ("raw", "frames")}
    if me is None:
        for ts, frame in found.get("frames") or []:
            source, dest, _hops, info = frame
            position = _position_of_frame(f"{source}>{dest}:{info}")
            if position:
                me = position

    # Who hears this station on RF: our receivers, the iGates in the APRS-IS stream, findu.com
    hear: dict[str, dict[str, Any]] = {}
    edges: collections.Counter[tuple[str, str]] = collections.Counter()

    def note(gate: str, ts: float, hops: list[str], source_name: str) -> None:
        chain = [call] + [hop.upper() for hop in used_hops(hops) if not ALIAS_RE.match(hop) and hop.upper() != call]
        if chain[-1] != gate:
            chain.append(gate)
        for a, b in zip(chain, chain[1:]):
            if a != b:
                edges[(a, b)] += 1
        entry = hear.setdefault(gate, {"call": gate, "n": 0, "direct": 0, "via": collections.Counter(), "last": 0.0, "src": set()})
        entry["n"] += 1
        entry["last"] = max(entry["last"], ts)
        entry["src"].add(source_name)
        sender = transmitter(hops)
        if not used_hops(hops) or (sender and sender.upper() == call):
            entry["direct"] += 1
        elif sender and sender != gate:
            entry["via"][sender] += 1
        else:
            entry["via"]["?"] += 1

    receiver = own_station or "APRSBox"
    # Our own receivers by interface name (2 m, LoRa): they sit at the own station's position.
    for name, band in bands.items():
        for ts, _interface, hops, _info in band["rx"]:
            note(name, ts, hops, "rx")
        if name in hear:
            hear[name]["ours"] = True
    # Another station's receive page (Settings -> Desktop application): its rows say via whom it heard the call.
    from app.desktop.remote_receivers import CHANNEL_NAMES, heard_rows

    remote_names = set()
    for row in heard_rows(call, since=now - window):
        name = f"{row['receiver']} ({CHANNEL_NAMES.get(row['channel'], row['channel'])})"
        via = str(row.get("via") or "")
        note(name, _ts(row["heard_at"]) or now, [] if via in ("", "RF") else [f"{via}*"], "remote")
        hear[name]["remote"] = row["receiver"]
        remote_names.add(row["receiver"])
    result["remote_receivers"] = sorted(remote_names)
    local = []
    for ts, _dest, path, q, igate, info in aprsis_own:
        if q in ("qAR", "qAO", "qAo") and igate and igate.upper() != receiver:
            note(igate.upper(), ts, path, "aprsis")
            local.append((igate.upper(), info, ts))
    for ts, path, igate, info in found.get("raw") or []:
        if igate.upper() == receiver or any(item[0] == igate.upper() and item[1] == info and abs(item[2] - ts) <= 90 for item in local):
            continue
        note(igate.upper(), ts, path, "findu")
    hearers = sorted(hear.values(), key=lambda entry: entry["n"], reverse=True)[:25]
    via_all = set(v for entry in hearers for v in entry["via"] if v != "?")
    for band in bands.values():
        for _moment, _kind, _dest, hops, _info in band["own"]:
            via_all.update(hop.upper() for hop in used_hops(hops) if not ALIAS_RE.match(hop))
    hearer_calls = {entry["call"] for entry in hearers if not entry.get("ours") and not entry.get("remote")}
    missing = [c for c in sorted(hearer_calls | via_all) if c not in known]
    if use_findu:
        for c in missing[:FINDU_POSITION_LOOKUPS]:
            try:
                position = _findu_position(c)
            except Exception:  # noqa: BLE001
                position = None
            if position:
                known[c] = position
    for entry in hearers:
        position = (known.get(receiver) if entry.get("ours") else
                    known.get(entry["remote"]) if entry.get("remote") else known.get(entry["call"]))
        entry["dist_km"] = round(dist_km(me[0], me[1], position[0], position[1]), 1) if (me and position) else None
        entry["via"] = entry["via"].most_common(6)
        entry["src"] = sorted(entry["src"])
    result["hearers"] = hearers
    infra = (hearer_calls | via_all) - {call}
    result["infra_near"] = sorted(c for c in infra if c in known and me and dist_km(me[0], me[1], known[c][0], known[c][1]) <= NEAR_INFRA_KM)

    # WIDE settings (the path sent, from the frames heard directly)
    own_frames = [item for band in bands.values() for item in band["own"]]
    paths = []
    for key, count in direct_paths.items():
        hops_count, old = path_hops(key.split(",") if key else [])
        direct_times = [ts for ts, _k, _d, hops, _i in own_frames if not used_hops(hops) and ",".join(hops) == key]
        paths.append({"path": key, "n": count, "hops": hops_count, "old": old, "last": path_last.get(key),
                      "first": min(direct_times) if direct_times else None})
    paths.sort(key=lambda row: row["last"] or 0, reverse=True)  # the newest one = the current setting
    paths = paths[:5]
    result["paths"] = paths

    # Verdicts (from the data only)
    heard = any(result["bands"][name]["own_unique"] for name in bands) or result["aprsis"]["n"] or result["findu"].get("n")
    if not heard and not any_digi:
        verdicts.append(("info", "No frames of this callsign in the period - neither in our reception nor in APRS-IS.", {}))
    if paths:
        current = paths[0]
        shown = current["path"] or "—"
        if (len(paths) > 1 and paths[0]["n"] >= 2 and paths[1]["n"] >= 2 and paths[0]["first"] and paths[1]["last"]
                and paths[0]["first"] > paths[1]["last"] - 120):
            verdicts.append(("info", "Path changed in the period: before {old}, lately {new}.",
                             {"old": paths[1]["path"] or "—", "new": shown}))
        if current["old"]:
            verdicts.append(("bad", "Outdated aliases in the path: {aliases} - replace them with WIDEn-N.",
                             {"aliases": ", ".join(current["old"])}))
        dense = len(result["infra_near"]) >= DENSE_MIN
        regional = REGIONAL_DIGIS.get(call)
        if regional and current["hops"] <= 2:
            verdicts.append(("ok", "Path {path} ({hops} hops) - {role}, the wider beacon coverage is intended.",
                             {"path": shown, "hops": current["hops"], "role": regional}))
        elif current["hops"] > 2:
            verdicts.append(("bad", "Path {path} asks for {hops} hops - too many (at most 2 recommended: WIDE1-1,WIDE2-1).",
                             {"path": shown, "hops": current["hops"]}))
        elif current["hops"] >= 2 and dense:
            near = ", ".join(result["infra_near"][:8]) + ("…" if len(result["infra_near"]) > 8 else "")
            verdicts.append(("bad", "Path {path} ({hops} hops) - too many: within {km} km there are {count} gateways/digipeaters "
                                    "that hear or repeat it ({near}). One hop (WIDE1-1) is enough, a fixed station often needs none.",
                             {"path": shown, "hops": current["hops"], "km": NEAR_INFRA_KM, "count": len(result["infra_near"]), "near": near}))
        elif any_digi and "WIDE1-1" in current["path"].split(","):
            verdicts.append(("warn", "The station is a digipeater itself and sends with WIDE1-1 - a fixed digipeater needs no WIDE1-1 "
                                     "fill-in, WIDE2-1 or no path is enough.", {}))
        elif current["hops"] == 0:
            verdicts.append(("ok", "Sends without a WIDE path - its frames are not multiplied by digipeaters.", {}))
        else:
            verdicts.append(("ok", "Path {path} ({hops} hops) - fine.", {"path": shown, "hops": current["hops"]}))
    for name, row in result["bands"].items():
        if row["copies_avg"] and row["copies_avg"] >= 2.5:
            verdicts.append(("bad", "{band}: every frame heard {copies} times on average - the path multiplies the traffic.",
                             {"band": name, "copies": row["copies_avg"]}))
        if row["beacon_iv_min"] is not None and row["beacon_iv_min"] < 12 and not result.get("mobile"):
            verdicts.append(("bad", "{band}: beacon every {minutes} min - too often for a fixed station (every 15 min or less often recommended).",
                             {"band": name, "minutes": row["beacon_iv_min"]}))
        if row["digi_dups"]:
            verdicts.append(("warn", "{band}: {count} frames repeated twice by this callsign within {seconds} s - weak duplicate filtering.",
                             {"band": name, "count": row["digi_dups"], "seconds": DUP_SEC}))
        if row["is2rf_other"]:
            verdicts.append(("bad", "{band}: put {count} internet frames on the air (positions/telemetry/status from APRS-IS) - wrong IS→RF filter.",
                             {"band": name, "count": row["is2rf_other"]}))
        elif row["is2rf_msg"]:
            verdicts.append(("ok", "{band}: passes only messages from the internet ({count}) - correct.",
                             {"band": name, "count": row["is2rf_msg"]}))
    if not any(row["is2rf_n"] for row in result["bands"].values()):
        verdicts.append(("ok", "We did not hear it put APRS-IS frames on the air (IS→RF).", {}))
    if result["aprsis"]["tcpip"] or (result["findu"].get("tcpip") or 0):
        verdicts.append(("info", "Some frames reach APRS-IS directly over the internet (TCPIP), not from radio.", {}))
    result["verdicts"] = [{"level": level, "text": text, "params": params} for level, text, params in verdicts]
    return result
