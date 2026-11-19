"""The station card lists every frame of the station the database keeps (the user: the whole
traffic retention, not the 10 frames found among the newest 500 of all traffic)."""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.db import TRAFFIC_RETENTION_MINUTES_SETTING_KEY, connection_scope, execute, init_db, set_app_setting
from app.services.content import get_recent_station_packets, get_station_packets_in_retention


@contextlib.contextmanager
def temporary_database() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "aprsbox-test.db"
        previous = os.environ.get("APRSBOX_DB_PATH")
        os.environ["APRSBOX_DB_PATH"] = str(database_path)
        try:
            init_db()
            yield database_path
        finally:
            if previous is None:
                os.environ.pop("APRSBOX_DB_PATH", None)
            else:
                os.environ["APRSBOX_DB_PATH"] = previous


def store(line: str, minutes_ago: float) -> None:
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat()
    execute(
        """
        INSERT INTO traffic_frames(source, interface_id, direction, band, format, line, port, command, length, hex, created_at)
        VALUES ('TNC-2m', NULL, 'rx', '2m', 'TNC2', ?, '0', 'RX', ?, '', ?)
        """,
        (line, len(line), created),
    )


def test_station_card_lists_the_whole_retention_window() -> None:
    with temporary_database(), connection_scope():
        set_app_setting(TRAFFIC_RETENTION_MINUTES_SETTING_KEY, "360")
        for index in range(15):  # SP8ABC-9 every 20 minutes over the last 5 hours
            store(f"SP8ABC-9>APRS,WIDE1-1:!5222.00N/02100.00E>Beacon {index}", 10 + 20 * index)
        store("SP8ABC-9>APRS:!5222.00N/02100.00E>Too old", 7 * 60)  # older than the retention
        store("SP8ABC-10>APRS:!5222.00N/02100.00E>Another SSID", 30)
        store("SP8XYZ>APRS,SP8ABC-9*,WIDE2-1:!5223.00N/02101.00E>Only a digi on the way", 40)
        for index in range(520):  # busy channel: the newest 500 frames hold none of SP8ABC-9
            store(f"SP8XYZ>APRS:!5223.00N/02101.00E>Traffic {index}", 1 + index * 0.01)

        snapshot = {"display_callsign": "SP8ABC-9"}
        packets = get_station_packets_in_retention("SP8ABC-9", snapshot=snapshot)

        assert [packet["raw_packet"].rsplit(">", 1)[-1] for packet in packets] == [f"Beacon {index}" for index in range(15)]
        assert [packet["timestamp"] for packet in packets] == sorted((packet["timestamp"] for packet in packets), reverse=True)
        assert get_recent_station_packets("SP8ABC-9", snapshot=snapshot) == []  # the web card's 500-frame look-back


def test_station_card_follows_the_retention_setting() -> None:
    with temporary_database(), connection_scope():
        store("SP8ABC-9>APRS:!5222.00N/02100.00E>Two hours ago", 120)
        store("SP8ABC-9>APRS:!5222.00N/02100.00E>Now", 1)
        snapshot = {"display_callsign": "SP8ABC-9"}
        set_app_setting(TRAFFIC_RETENTION_MINUTES_SETTING_KEY, "60")
        assert [packet["raw_packet"][-3:] for packet in get_station_packets_in_retention("SP8ABC-9", snapshot=snapshot)] == ["Now"]
        set_app_setting(TRAFFIC_RETENTION_MINUTES_SETTING_KEY, "180")
        assert len(get_station_packets_in_retention("SP8ABC-9", snapshot=snapshot)) == 2
