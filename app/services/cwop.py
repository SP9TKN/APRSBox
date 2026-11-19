"""CWOP weather stations on the map (the user's choice, switched on in Settings).

Citizen Weather Observer Program stations (CW1234, DW..., EW..., FW...) report to the CWOP servers,
not to the APRS-IS core, so the APRS-IS uplink never sees them (APRSIS32 had a separate "CWOP" port
for them). This client logs in to cwop.aprs.net read-only (passcode -1) with a range filter around
My Station - the same radius as the APRS-IS filter (m/50 -> 50 km; the user's choice), so a change
there applies to the weather stations too. Its frames reach the traffic monitor and the map, never
the routing: no digipeating, no IGate, nothing is transmitted. Its status is kept apart from the
APRS-IS uplink's.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import socket
import time
from typing import Any

from app.db import connection_scope, fetch_one, get_app_setting, log_event, set_app_setting, utc_now
from app.services.aprsis import AprsisClientService, get_aprsis_config

CWOP_SERVER = "cwop.aprs.net"
CWOP_PORT = 14580
CWOP_ENABLED_SETTING_KEY = "cwop_enabled"
CWOP_STATUS_SETTING_KEY = "cwop_runtime_status"
DEFAULT_CWOP_RANGE_KM = 50  # when the APRS-IS filter has no radius (APRSBox's own default is m/50 here)
# cwop.aprs.net is several servers. One of them (CWOP-4, 2026-10-10) answers "# No user-specified
# filters on this port" and sends the whole world - 10 000 weather stations, 18 000 frames an hour.
# Such a server is left for a while and another one is used; and whatever a server sends, only
# frames with a position inside the radius get in.
CWOP_NO_FILTER_MARKER = "no user-specified filters"
CWOP_BLOCK_SECONDS = 6 * 3600
CWOP_CONNECT_TIMEOUT_S = 6.0
CWOP_RANGE_MARGIN_KM = 1.0
_POSITION_RE = re.compile(r"(\d{2})(\d{2}\.\d{1,2})([NS]).(\d{3})(\d{2}\.\d{1,2})([EW])")
_MY_RANGE_RE = re.compile(r"(?:^|\s)m/(\d+(?:\.\d+)?)")
_RANGE_RE = re.compile(r"(?:^|\s)r/-?[\d.]+/-?[\d.]+/(\d+(?:\.\d+)?)")


def aprsis_range_km() -> int:
    """The radius of the APRS-IS interface filter (m/<km> or r/<lat>/<lon>/<km>)."""
    row = fetch_one("SELECT device_path FROM modems WHERE UPPER(modem_type) = 'APRSIS' ORDER BY enabled DESC, id ASC LIMIT 1")
    server_filter = str((row["device_path"] if row else "") or "")
    match = _MY_RANGE_RE.search(server_filter) or _RANGE_RE.search(server_filter)
    return max(1, round(float(match.group(1)))) if match else DEFAULT_CWOP_RANGE_KM


def get_cwop_settings() -> dict[str, Any]:
    return {"enabled": str(get_app_setting(CWOP_ENABLED_SETTING_KEY) or "0") == "1", "range_km": aprsis_range_km()}


def save_cwop_settings(*, enabled: bool) -> None:
    set_app_setting(CWOP_ENABLED_SETTING_KEY, "1" if enabled else "0")


def get_cwop_status() -> dict[str, str]:
    try:
        value = json.loads(get_app_setting(CWOP_STATUS_SETTING_KEY) or "{}")
    except ValueError:
        value = {}
    return {key: str(value.get(key) or "") for key in ("status", "detail", "at")}


def cwop_filter(latitude: float, longitude: float, range_km: int) -> str:
    return f"r/{latitude:.4f}/{longitude:.4f}/{int(range_km)}"


def frame_position(line: str) -> tuple[float, float] | None:
    """The plain (uncompressed) position of a TNC2 line, as CWOP weather stations send it."""
    _header, separator, info = line.partition(":")
    match = _POSITION_RE.search(info) if separator else None
    if match is None:
        return None
    latitude = int(match.group(1)) + float(match.group(2)) / 60
    longitude = int(match.group(4)) + float(match.group(5)) / 60
    if match.group(3) == "S":
        latitude = -latitude
    if match.group(6) == "W":
        longitude = -longitude
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((phi2 - phi1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _coordinate(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class CwopReceiverService(AprsisClientService):
    _source_label = "CWOP"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.pop("frame_consumer", None)  # never routed
        super().__init__(**kwargs)
        self._last_status: tuple[str, str] | None = None
        self._blocked_addresses: dict[str, float] = {}  # server address -> monotonic time it may be used again
        self._outside_dropped = 0
        self._outside_reported_at = 0.0

    def set_frame_consumer(self, frame_consumer: Any) -> None:
        self._frame_consumer = None  # CWOP frames never reach the digipeater, IGate or IS->RF

    def _read_desired_state(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        from app.services.content import get_station_settings

        with connection_scope():
            settings = get_cwop_settings()
            station = get_station_settings(include_tx_runtime=False)
            login = str(get_aprsis_config().get("login") or "")
        config = {"server": CWOP_SERVER, "port": CWOP_PORT, "login": login, "passcode": "-1"}
        latitude, longitude = _coordinate(station.get("latitude")), _coordinate(station.get("longitude"))
        if not settings["enabled"] or latitude is None or longitude is None:
            return None, config
        server_filter = cwop_filter(latitude, longitude, settings["range_km"])
        return {"id": None, "name": CWOP_SERVER, "filter": server_filter, "effective_filter": server_filter,
                "center": (latitude, longitude), "range_km": float(settings["range_km"])}, config

    async def _open_connection(self, server: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect to one of the CWOP servers, skipping those that ignored the filter lately."""
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(loop.getaddrinfo(server, port, type=socket.SOCK_STREAM), timeout=CWOP_CONNECT_TIMEOUT_S)
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        random.shuffle(addresses)  # spread over the pool, as the DNS round robin does
        now = time.monotonic()
        usable = [address for address in addresses if self._blocked_addresses.get(address, 0.0) <= now]
        last_error: BaseException = OSError(f"no address for {server}")
        for address in usable or addresses:
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port), timeout=CWOP_CONNECT_TIMEOUT_S)
            except (OSError, TimeoutError) as exc:
                last_error = exc
        raise last_error

    def _process_server_line(self, raw_line: bytes | str) -> bool:
        line = raw_line.decode("latin-1") if isinstance(raw_line, bytes) else str(raw_line or "")
        text = line.strip()
        if text.startswith("#"):
            if CWOP_NO_FILTER_MARKER in text.lower():
                address = self._peer_address()
                if address:
                    self._blocked_addresses[address] = time.monotonic() + CWOP_BLOCK_SECONDS
                log_event("WARNING", "aprsis", f"CWOP server {address or '?'} ignores the range filter ({text}); "
                                               f"connecting to another CWOP server")
                raise ValueError("CWOP server ignores the range filter")
            return False
        rx_interface = self._desired_rx_interface or {}
        center, range_km = rx_interface.get("center"), rx_interface.get("range_km")
        position = frame_position(text) if text else None
        if center and range_km and position is not None:
            if distance_km(center[0], center[1], position[0], position[1]) > float(range_km) + CWOP_RANGE_MARGIN_KM:
                self._note_outside()
                return False
        return super()._process_server_line(raw_line)

    def _peer_address(self) -> str:
        writer = self._writer
        try:
            peer = writer.get_extra_info("peername") if writer is not None else None
        except Exception:
            peer = None
        return str(peer[0]) if peer else ""

    def _note_outside(self) -> None:
        self._outside_dropped += 1
        now = time.monotonic()
        if now - self._outside_reported_at >= 3600:
            self._outside_reported_at = now
            log_event("WARNING", "aprsis", f"CWOP: {self._outside_dropped} frames from outside the radius dropped "
                                           f"(the server does not filter)")

    @staticmethod
    def _rx_signature(rx_interface: dict[str, Any] | None) -> tuple[int, str] | None:
        if rx_interface is None:
            return None
        return 0, str(rx_interface.get("effective_filter") or "")

    def _persist_status(self, **status: Any) -> None:
        state = (str(status.get("status") or ""), str(status.get("status_detail") or ""))
        if state == self._last_status:  # the run loop reports "inactive" every second while off
            return
        self._last_status = state
        set_app_setting(CWOP_STATUS_SETTING_KEY, json.dumps({"status": state[0], "detail": state[1], "at": utc_now()}))
