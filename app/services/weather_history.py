"""Weather history for the station charts (the user's choice): every weather report heard over RF,
APRS-IS or CWOP is kept for 7 days in metric units, longer than the traffic frames (Settings ->
traffic history retention). One reading per station and minute: the same report heard over RF and
APRS-IS is stored once. The first start fills it from the frames still in the database.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import connection_scope, fetch_all, fetch_one, get_app_setting, get_connection, set_app_setting

WEATHER_HISTORY_DAYS = 7
BACKFILL_SETTING_KEY = "weather_history_backfilled"
PRUNE_INTERVAL_SECONDS = 600
VALUE_COLUMNS = (
    "temperature_c", "humidity_percent", "pressure_hpa", "wind_dir_deg", "wind_speed_ms", "wind_gust_ms",
    "rain_1h_mm", "rain_24h_mm", "rain_midnight_mm", "luminosity_wm2",
)
_WEATHER_KEYS = (
    "temperature_f", "humidity_percent", "pressure_hpa", "wind_speed_mph", "wind_gust_mph",
    "rain_1h_in", "rain_24h_in", "rain_since_midnight_in", "luminosity_wm2",
)
_MPH_TO_MS = 0.44704
_INCH_TO_MM = 25.4
_last_prune = 0.0


def _within(value: Any, low: float, high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def observation_from_parsed(parsed: dict[str, Any] | None) -> tuple[str, dict[str, float | None]] | None:
    """Station key and metric readings of a weather report; None for any other frame."""
    if not parsed:
        return None
    data = dict((parsed.get("aprs_data") or {}).get("data") or {})
    if not any(key in data for key in _WEATHER_KEYS):
        return None
    station_key = str(parsed.get("entity_name") or parsed.get("logical_source_key") or parsed.get("source_key") or "").strip()
    if not station_key:
        return None

    def scaled(key: str, factor: float, low: float, high: float) -> float | None:
        value = _within(data.get(key), -1e9, 1e9)
        return None if value is None else _within(value * factor, low, high)

    temperature_f = _within(data.get("temperature_f"), -100, 160)
    pressure = _within(data.get("pressure_hpa"), 850, 1090)  # b00000: no barometer
    humidity = _within(data.get("humidity_percent"), 1, 100)
    if humidity == 100 and pressure is None and data.get("pressure_hpa") is not None:
        humidity = None  # h00 together with b00000: a station without these sensors, not 100 %
    values = {
        "temperature_c": None if temperature_f is None else round((temperature_f - 32) * 5 / 9, 2),
        "humidity_percent": humidity,
        "pressure_hpa": pressure,
        "wind_dir_deg": _within(data.get("wind_dir"), 0, 360),
        "wind_speed_ms": scaled("wind_speed_mph", _MPH_TO_MS, 0, 100),
        "wind_gust_ms": scaled("wind_gust_mph", _MPH_TO_MS, 0, 150),
        "rain_1h_mm": scaled("rain_1h_in", _INCH_TO_MM, 0, 500),
        "rain_24h_mm": scaled("rain_24h_in", _INCH_TO_MM, 0, 2000),
        "rain_midnight_mm": scaled("rain_since_midnight_in", _INCH_TO_MM, 0, 2000),
        "luminosity_wm2": _within(data.get("luminosity_wm2"), 0, 2000),
    }
    if all(value is None for value in values.values()):
        return None
    return station_key, values


def record_weather_observation(connection: sqlite3.Connection, *, parsed: dict[str, Any] | None,
                               observed_at: str, source_kind: str = "") -> bool:
    observation = observation_from_parsed(parsed)
    if observation is None:
        return False
    station_key, values = observation
    stamp = str(observed_at or "")
    connection.execute(
        f"""
        INSERT OR IGNORE INTO weather_observations(station_key, observed_at, minute, source_kind, {", ".join(VALUE_COLUMNS)})
        VALUES (?, ?, ?, ?, {", ".join("?" for _ in VALUE_COLUMNS)})
        """,
        (station_key, stamp, stamp[:16], str(source_kind or ""), *(values[column] for column in VALUE_COLUMNS)),
    )
    return True


def prune_weather_observations(*, now: datetime | None = None, force: bool = False) -> int:
    """Drop readings older than the history window (at most every PRUNE_INTERVAL_SECONDS)."""
    global _last_prune
    if not force and time.monotonic() - _last_prune < PRUNE_INTERVAL_SECONDS:
        return 0
    _last_prune = time.monotonic()
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=WEATHER_HISTORY_DAYS)).isoformat()
    with get_connection() as connection:
        return connection.execute("DELETE FROM weather_observations WHERE observed_at < ?", (cutoff,)).rowcount


def backfill_weather_history() -> int:
    """Once: the weather reports among the frames already in the database (the last hours)."""
    if get_app_setting(BACKFILL_SETTING_KEY) == "1":
        return 0
    from app.services.content import parse_tnc2_frame

    stored = 0
    with connection_scope() as connection:
        rows = connection.execute(
            "SELECT line, source_kind, created_at FROM traffic_frames WHERE format = 'TNC2' AND direction = 'rx' ORDER BY id"
        ).fetchall()
        for row in rows:
            line = str(row["line"] or "")
            if "_" not in line:  # every weather report has the weather symbol or a positionless _ report
                continue
            if record_weather_observation(connection, parsed=parse_tnc2_frame(line),
                                          observed_at=str(row["created_at"] or ""), source_kind=str(row["source_kind"] or "")):
                stored += 1
        set_app_setting(BACKFILL_SETTING_KEY, "1")
    return stored


def get_weather_history(station_key: str, *, hours: int = WEATHER_HISTORY_DAYS * 24) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = fetch_all(
        f"""
        SELECT observed_at, {", ".join(VALUE_COLUMNS)}
        FROM weather_observations
        WHERE station_key = ? AND observed_at >= ?
        ORDER BY observed_at ASC
        """,
        (str(station_key or "").strip(), cutoff),
    )
    return [dict(row) for row in rows]


def station_has_weather_history(station_key: str) -> bool:
    row = fetch_one("SELECT 1 FROM weather_observations WHERE station_key = ? LIMIT 1", (str(station_key or "").strip(),))
    return row is not None
