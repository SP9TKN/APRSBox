"""Traffic monitor logs: continuous per-interface log files and exports.

Continuous logging (switched on per interface) appends every frame of the traffic monitor
to ``logs/traffic/<interface>/<YYYY-MM-DD>.log``, one file per UTC day, like Direwolf and
APRSIS32 keep packet logs.  The database itself keeps only the configured traffic
retention (Settings), so the files are the long-term history.  Exports write the frames
still held in the database (optionally one interface, a time range and a direction) as
text or CSV.  Times are UTC, as everywhere in APRSBox.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.datetime_utils import parse_datetime
from app.db import connection_scope, fetch_all, fetch_one, get_app_setting, log_event, set_app_setting

ENABLED_INTERFACES_SETTING_KEY = "desktop.traffic_log.interfaces"
POLL_SECONDS = 2.0
BATCH_LIMIT = 5000
EXPORT_FORMATS = ("txt", "csv")
DIRECTION_TX_REMOTE = "tx-remote"
_FRAME_COLUMNS = "id, interface_id, direction, source, source_kind, format, line, command, hex, created_at"
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ------------------------------------------------------------------ settings
def enabled_interface_ids() -> set[int]:
    try:
        values = json.loads(get_app_setting(ENABLED_INTERFACES_SETTING_KEY) or "[]")
        return {int(value) for value in values}
    except (TypeError, ValueError):
        return set()


def set_interface_logging(interface_id: int, enabled: bool) -> None:
    ids = enabled_interface_ids()
    if enabled == (int(interface_id) in ids):
        return
    ids = ids | {int(interface_id)} if enabled else ids - {int(interface_id)}
    set_app_setting(ENABLED_INTERFACES_SETTING_KEY, json.dumps(sorted(ids)))
    log_event("INFO", "traffic_log", f"Continuous traffic log {'enabled' if enabled else 'disabled'} for interface {interface_id}")


def log_root() -> Path:
    return Path(settings.log_dir) / "traffic"


def interface_log_dir(name: str, interface_id: int) -> Path:
    safe = _UNSAFE_NAME.sub("_", str(name or "")).strip(" .")
    return log_root() / (safe or f"interface-{interface_id}")


def interface_names() -> dict[int, str]:
    return {int(row["id"]): str(row["name"] or "") for row in fetch_all("SELECT id, name FROM modems")}


# ------------------------------------------------------------------ formatting
def tnc2(line: str) -> str:
    """Standard TNC2 text: the monitor shows the header as ``SRC > DST , PATH``."""
    header, separator, info = str(line or "").partition(":")
    if not separator:
        return str(line or "")
    header = re.sub(r"\s*>\s*", ">", header, count=1)
    header = re.sub(r"\s*,\s*", ",", header)
    return f"{header}{separator}{info}"


def direction_label(frame: dict[str, Any]) -> str:
    if str(frame.get("command") or "").strip().upper().startswith("TX-PROXY"):
        return "TX-REMOTE"
    return str(frame.get("direction") or "").strip().upper() or "-"


def frame_time(frame: dict[str, Any]) -> datetime:
    parsed = parse_datetime(frame.get("created_at"))
    return (parsed or datetime.now(timezone.utc)).astimezone(timezone.utc)


def frame_text(frame: dict[str, Any]) -> str:
    text = tnc2(str(frame.get("line") or "")).strip()
    if str(frame.get("format") or "").upper() != "TNC2" and frame.get("hex"):
        text = f"{text} [hex {frame['hex']}]".strip()
    return text


def format_line(frame: dict[str, Any], *, interface: str | None = None) -> str:
    stamp = frame_time(frame).strftime("%Y-%m-%d %H:%M:%S")
    tag = f" [{interface}]" if interface is not None else ""
    return f"{stamp}{tag} {direction_label(frame):<9} {frame_text(frame)}"


def csv_row(frame: dict[str, Any], interface: str) -> list[str]:
    text = tnc2(str(frame.get("line") or ""))
    header, _separator, info = text.partition(":")
    source, _arrow, rest = header.partition(">")
    destination, _comma, path = rest.partition(",")
    return [
        frame_time(frame).strftime("%Y-%m-%d %H:%M:%S"),
        interface,
        direction_label(frame),
        str(frame.get("source_kind") or ""),
        source.strip(),
        destination.strip(),
        path.strip(),
        info,
        str(frame.get("hex") or ""),
    ]


CSV_HEADER = ["time_utc", "interface", "direction", "source_kind", "source", "destination", "path", "info", "hex"]


# ------------------------------------------------------------------ export
def export_frames(
    target: Path,
    *,
    interface_id: int | None = None,
    minutes: int = 0,
    direction: str = "",
    fmt: str = "txt",
) -> int:
    """Write the frames still in the database to ``target``; returns how many were written.

    ``minutes`` 0 = everything the database keeps; ``direction`` "", "rx", "tx" or "tx-remote".
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {fmt}")
    clauses: list[str] = []
    params: list[Any] = []
    if interface_id is not None:
        clauses.append("interface_id = ?")
        params.append(int(interface_id))
    if minutes and minutes > 0:
        since = datetime.now(timezone.utc) - timedelta(minutes=int(minutes))
        clauses.append("created_at >= ?")
        params.append(since.replace(microsecond=0).isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = [dict(row) for row in fetch_all(f"SELECT {_FRAME_COLUMNS} FROM traffic_frames {where} ORDER BY id", tuple(params))]
    if direction:
        rows = [row for row in rows if direction_label(row).lower() == direction.lower()]
    names = interface_names()
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        # Semicolons and a BOM: opens directly in a Polish Excel.
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(CSV_HEADER)
            for row in rows:
                writer.writerow(csv_row(row, names.get(row.get("interface_id"), "")))
    else:
        with target.open("w", encoding="utf-8") as handle:
            handle.write(f"# APRSBox traffic export, {len(rows)} frames, times UTC\n")
            for row in rows:
                handle.write(format_line(row, interface=names.get(row.get("interface_id"), "-")) + "\n")
    log_event("INFO", "traffic_log", f"Exported {len(rows)} traffic frames to {target}")
    return len(rows)


# ------------------------------------------------------------------ continuous log
class TrafficLogWriter:
    """Background thread appending new traffic_frames rows to the per-interface daily files."""

    def __init__(self, *, poll_seconds: float = POLL_SECONDS) -> None:
        self.poll_seconds = poll_seconds
        self.last_id = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Only frames from now on: older rows were logged by the previous session (if enabled).
        row = fetch_one("SELECT COALESCE(MAX(id), 0) AS last_id FROM traffic_frames")
        self.last_id = int(row["last_id"]) if row else 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aprsbox-traffic-log", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self.run_once()  # frames that arrived after the last poll

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.run_once()
            except Exception as exc:  # never let logging break the program
                try:
                    log_event("WARNING", "traffic_log", f"Traffic log write failed: {exc}")
                except Exception:
                    pass

    def run_once(self) -> int:
        with connection_scope():  # every POLL_SECONDS: one connection for the settings, frames and names
            enabled = enabled_interface_ids()
            rows = [
                dict(row)
                for row in fetch_all(
                    f"SELECT {_FRAME_COLUMNS} FROM traffic_frames WHERE id > ? ORDER BY id LIMIT ?",
                    (self.last_id, BATCH_LIMIT),
                )
            ]
            if not rows:
                return 0
            self.last_id = int(rows[-1]["id"])
            rows = [row for row in rows if row.get("interface_id") in enabled]
            if not rows:
                return 0
            names = interface_names()
        batches: dict[Path, list[str]] = {}
        for row in rows:
            interface_id = int(row["interface_id"])
            folder = interface_log_dir(names.get(interface_id, ""), interface_id)
            path = folder / f"{frame_time(row).strftime('%Y-%m-%d')}.log"
            batches.setdefault(path, []).append(format_line(row))
        for path, lines in batches.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists()
            with path.open("a", encoding="utf-8") as handle:
                if new_file:
                    handle.write(f"# APRSBox traffic log: {path.parent.name}, {path.stem}, times UTC\n")
                handle.write("\n".join(lines) + "\n")
        return len(rows)
