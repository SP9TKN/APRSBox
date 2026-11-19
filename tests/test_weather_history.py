"""Weather history for the station charts: metric readings of every weather report, kept 7 days."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FW0001 = ("FW0001>APN000,TCPXX*,qAX,CWOP-7:@091511z5007.88N/01953.08E_179/001g006t056r000p000P003b10147h63"
          "L027eMB54")
NO_SENSORS = "SR9XWX>AKLPRZ:!4934.08N/02002.32E_019/000g000t050r...p...P...b00000h00"


@pytest.fixture()
def weather_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APRSBOX_DB_PATH", str(tmp_path / "data" / "aprsbox.db"))
    from app.db import init_db

    init_db()


def test_a_weather_report_becomes_metric_readings() -> None:
    from app.services.content import parse_tnc2_frame
    from app.services.weather_history import observation_from_parsed

    station, values = observation_from_parsed(parse_tnc2_frame(FW0001))
    assert station == "FW0001"
    assert values["temperature_c"] == pytest.approx(13.33, abs=0.01)  # t056 °F
    assert values["wind_dir_deg"] == 179 and values["wind_speed_ms"] == pytest.approx(0.447, abs=0.001)  # _179/001
    assert values["wind_gust_ms"] == pytest.approx(2.68, abs=0.01)
    assert (values["humidity_percent"], values["pressure_hpa"], values["luminosity_wm2"]) == (63, 1014.7, 27)
    assert values["rain_midnight_mm"] == pytest.approx(0.76, abs=0.01)
    assert observation_from_parsed(parse_tnc2_frame("SP9ABC>APRS:!5005.67N/01940.55E>mobile")) is None


def test_missing_sensors_are_not_readings() -> None:
    from app.services.content import parse_tnc2_frame
    from app.services.weather_history import observation_from_parsed

    _station, values = observation_from_parsed(parse_tnc2_frame(NO_SENSORS))
    assert values["pressure_hpa"] is None  # b00000
    assert values["humidity_percent"] is None  # h00 with no barometer: not 100 %
    assert values["temperature_c"] == pytest.approx(10.0)


def test_received_reports_are_stored_once_a_minute_and_kept_seven_days(weather_db: None) -> None:
    from app.db import fetch_one
    from app.services.traffic import process_normalized_tnc2_rx
    from app.services.weather_history import get_weather_history, prune_weather_observations

    now = datetime.now(timezone.utc).replace(microsecond=0)
    minute = now.replace(second=0) - timedelta(minutes=5)
    for seconds, kind in ((5, "aprsis"), (25, "rf")):  # the same report over APRS-IS and RF, one minute
        assert process_normalized_tnc2_rx(FW0001, source="test", source_kind=kind,
                                          timestamp=(minute + timedelta(seconds=seconds)).isoformat())
    old = (now - timedelta(days=8)).isoformat()
    assert process_normalized_tnc2_rx(FW0001, source="test", source_kind="aprsis", timestamp=old)
    rows = get_weather_history("FW0001")
    assert len(rows) == 1 and rows[0]["temperature_c"] == pytest.approx(13.33, abs=0.01)  # 8 days ago is outside
    assert prune_weather_observations(force=True) == 1
    assert fetch_one("SELECT COUNT(*) AS n FROM weather_observations")["n"] == 1


def test_first_start_fills_the_history_from_the_stored_frames_once(weather_db: None) -> None:
    from app.db import execute
    from app.services.weather_history import backfill_weather_history, get_weather_history

    stamp = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()
    execute(
        """
        INSERT INTO traffic_frames(source, source_kind, interface_id, direction, band, format, line, port, command, length, hex, created_at)
        VALUES ('Test', 'aprsis', NULL, 'rx', '', 'TNC2', ?, '0', 'RX', ?, '', ?)
        """,
        (FW0001, len(FW0001), stamp),
    )
    assert backfill_weather_history() == 1
    assert backfill_weather_history() == 0  # once
    assert [row["observed_at"] for row in get_weather_history("FW0001")] == [stamp]
