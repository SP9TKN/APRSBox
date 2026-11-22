"""Another station's receive page as an extra listener for the traffic analyzer (the user's choice, 2026-11-22).

SP9DLM's APRS ANALIZER page (port 8080) has no frame feed, but /api/stats lists the last 40 decoded rows per
band (time, callsign, via whom, WIDE path, device). APRSBox fetches it every minute and keeps the rows for good,
so the analyzer can tell whether that station heard a callsign, directly or through which digipeater. Read
only: nothing is ever sent to it. The page counts each address that asked in the last minute as one of its
10 viewers, so polling every minute holds one place there.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable

from app.db import fetch_all, get_app_setting, get_connection, log_event, set_app_setting, utc_now

URL_SETTING_KEY = "remote_receiver.url"
POLL_SECONDS = 60
FETCH_TIMEOUT_S = 15
ERROR_REPORT_EVERY_S = 3600  # a page that is down writes one warning an hour, not one a minute
CHANNEL_NAMES = {"lora": "LoRa 434,855", "wx": "144.800"}  # the bands of SP9DLM's page


def remote_url() -> str:
    try:
        return str(get_app_setting(URL_SETTING_KEY) or "").strip()
    except Exception:  # noqa: BLE001 - no database yet
        return ""


def normalize_url(value: Any) -> str:
    """'' (off) or 'http(s)://host[:port][/path]' without a trailing slash."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Enter the page address, e.g. http://station.example:8080")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def set_remote_url(value: Any) -> str:
    url = normalize_url(value)
    set_app_setting(URL_SETTING_KEY, url)
    return url


def receiver_name(url: str) -> str:
    """'http://sp9xyz.example.net:8080' -> 'SP9XYZ' (the first label of the host)."""
    host = urllib.parse.urlsplit(url).hostname or url
    return host.split(".", 1)[0].upper()


def store_rows(receiver: str, rows: Iterable[dict[str, Any]]) -> int:
    """Rows {channel, t, call, path, wide, dev}; the same row fetched again is kept once."""
    stored = 0
    now = utc_now()
    with get_connection() as connection:
        for row in rows:
            try:
                heard_at = datetime.fromtimestamp(float(row["t"]), timezone.utc).replace(microsecond=0).isoformat()
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
            call = str(row.get("call") or "").strip().upper()
            channel = str(row.get("channel") or "").strip().lower()
            if not call or not channel:
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO remote_heard(receiver, channel, heard_at, call, via, wide, device, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (receiver, channel, heard_at, call, str(row.get("path") or "").strip().upper(),
                 str(row.get("wide") or "").strip(), str(row.get("dev") or "").strip(), now),
            )
            stored += cursor.rowcount
    return stored


def rows_of_stats(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in CHANNEL_NAMES:
        for row in (payload.get(channel) or {}).get("rx") or []:
            if isinstance(row, dict):
                rows.append({**row, "channel": channel})
    return rows


def fetch_stats(url: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{url}/api/stats", headers={"User-Agent": "APRSBox-remote-receiver/1.0"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310 - the configured page
        return json.loads(response.read().decode("utf-8", errors="replace"))


def poll_once() -> int:
    url = remote_url()
    if not url:
        return 0
    payload = fetch_stats(url)
    if payload.get("busy"):
        raise RuntimeError("the page has its maximum number of viewers")
    return store_rows(receiver_name(url), rows_of_stats(payload))


def heard_rows(call: str, *, since: float) -> list[dict[str, Any]]:
    """What the remote receivers heard from ``call`` since ``since`` (epoch), oldest first."""
    since_iso = datetime.fromtimestamp(since, timezone.utc).replace(microsecond=0).isoformat()
    return [
        dict(row)
        for row in fetch_all(
            "SELECT receiver, channel, heard_at, call, via, wide, device FROM remote_heard "
            "WHERE call = ? AND heard_at >= ? ORDER BY heard_at",
            (str(call or "").strip().upper(), since_iso),
        )
    ]


def monitor_rows(*, since_iso: str, limit: int = 400) -> list[dict[str, Any]]:
    """The newest rows since ``since_iso`` as traffic-log entries (newest first, like the traffic snapshot)."""
    from app.services.content import format_display_datetime

    rows = fetch_all(
        "SELECT receiver, channel, heard_at, call, via, wide, device FROM remote_heard "
        "WHERE heard_at >= ? ORDER BY heard_at DESC, id DESC LIMIT ?",
        (since_iso, max(1, int(limit))),
    )
    entries = []
    for row in rows:
        via = str(row["via"] or "")
        parts = [str(row["call"]), "RF" if via in ("", "RF") else f"via {via}"]
        if row["wide"] and row["wide"] not in ("—", "-"):
            parts.append(str(row["wide"]))
        if row["device"]:
            parts.append(str(row["device"]))
        entries.append({
            "id": None,
            "timestamp": format_display_datetime(row["heard_at"]) or str(row["heard_at"]),
            "created_at": str(row["heard_at"]),
            "source": str(row["receiver"]),
            "source_kind": "remote",
            "interface_id": None,
            "remote_key": f"remote:{row['receiver']}",
            "direction": "RX",
            "band": CHANNEL_NAMES.get(str(row["channel"]), str(row["channel"])),
            "format": "REMOTE",
            "line": "  ·  ".join(parts),
            "port": "",
            "command": "REMOTE",
            "length": "",
            "hex": "",
            "row_class": "traffic-log-row-remote",
        })
    return entries


def monitor_interface() -> dict[str, Any] | None:
    """The receive page as a row of the traffic monitor's interface list (None while no address is set)."""
    url = remote_url()
    if not url:
        return None
    from app.db import fetch_one
    from app.services.content import format_display_datetime

    name = receiver_name(url)
    last = fetch_one("SELECT MAX(created_at) AS last FROM remote_heard WHERE receiver = ?", (name,))
    last_at = str(last["last"] or "") if last else ""
    fresh = False
    if last_at:
        try:
            fresh = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).total_seconds() <= 10 * 60
        except ValueError:
            fresh = False
    return {
        "modem_id": None,
        "remote_key": f"remote:{name}",
        "name": name,
        "band": " / ".join(CHANNEL_NAMES.values()),
        "url": url,
        "status": "connected" if fresh else "idle",
        "last_new_row": format_display_datetime(last_at) if last_at else "",
        "expose": {},
    }


class RemoteReceiverPoller:
    """Background thread: the configured receive page every POLL_SECONDS (idle while no address is set)."""

    def __init__(self, *, poll_seconds: float = POLL_SECONDS) -> None:
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error_report = 0.0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aprsbox-remote-receiver", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                poll_once()
            except Exception as exc:  # noqa: BLE001 - a remote page that is down never disturbs the station
                now = time.monotonic()
                if now - self._last_error_report >= ERROR_REPORT_EVERY_S:
                    self._last_error_report = now
                    try:
                        log_event("WARNING", "remote_receiver", f"Remote receiver page {remote_url()} not read: {exc!r}")
                    except Exception:
                        pass
