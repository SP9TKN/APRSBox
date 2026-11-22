"""Traffic analyzer of the station card (SP9XDG's rules on APRSBox data)."""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import execute, init_db


@contextlib.contextmanager
def station_data():
    with tempfile.TemporaryDirectory() as temp_dir:
        saved = {key: os.environ.get(key) for key in ("APRSBOX_DB_PATH", "APRSBOX_LOG_DIR")}
        os.environ["APRSBOX_DB_PATH"] = str(Path(temp_dir) / "aprsbox-test.db")
        os.environ["APRSBOX_LOG_DIR"] = str(Path(temp_dir) / "logs")
        try:
            init_db()
            import desktop_support

            desktop_support.seed_station_settings(callsign="SP9TKN")
            execute("INSERT INTO modems(name, modem_type, band, device_path, enabled, notes, created_at, updated_at) "
                    "VALUES ('APRS 2M', 'TCP', '2m', '127.0.0.1:1', 1, '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
            yield Path(temp_dir)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def frame(minutes_ago: float, line: str, direction: str = "rx", kind: str = "rf") -> None:
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat()
    execute(
        "INSERT INTO traffic_frames(source, source_kind, interface_id, direction, band, format, line, port, command, length, hex, "
        "created_at) VALUES (?, ?, ?, ?, '2m', 'TNC2', ?, '0', ?, 10, '', ?)",
        ("APRS-IS" if kind == "aprsis" else "APRS 2M", kind, None if kind == "aprsis" else 1, direction, line,
         "TX" if direction == "tx" else "RX", created),
    )


def test_a_fixed_station_beaconing_often_with_a_long_path_is_flagged() -> None:
    from app.desktop.station_analysis import analyze_station

    with station_data():
        for index in range(5):  # every 5 min, each heard directly and twice more through digipeaters
            minutes = 50 - index * 5
            info = f"!5005.00N/01940.00E-QTH {index}"
            frame(minutes, f"SP9ABC>APRS,WIDE2-2:{info}")
            frame(minutes - 0.1, f"SP9ABC>APRS,SR9XSR*,WIDE2-1:{info}")
            frame(minutes - 0.2, f"SP9ABC>APRS,SR9XSR,SP9XDG*,WIDE2*:{info}")
        frame(20, "SP9ABC>APRS,TCPIP*,qAC,T2POLAND:>via the internet", kind="aprsis")
        result = analyze_station("SP9ABC", hours=1)
    band = result["bands"]["APRS 2M"]
    assert (band["own_unique"], band["own_heard"], band["copies_avg"], band["beacon_iv_min"]) == (5, 15, 3.0, 5.0)
    assert dict(band["via"]) == {"SR9XSR": 5, "SP9XDG": 5}  # once per transmission
    assert result["paths"][0]["path"] == "WIDE2-2" and result["paths"][0]["hops"] == 2
    texts = {verdict["text"] for verdict in result["verdicts"]}
    assert "{band}: every frame heard {copies} times on average - the path multiplies the traffic." in texts
    assert "{band}: beacon every {minutes} min - too often for a fixed station (every 15 min or less often recommended)." in texts
    assert "Some frames reach APRS-IS directly over the internet (TCPIP), not from radio." in texts
    assert result["aprsis"]["tcpip"] == 1


def test_longer_periods_read_the_daily_traffic_logs() -> None:
    from app.desktop.station_analysis import analyze_station

    with station_data() as root:
        # Older than the retention (1 h in a new database): only in the daily log of the interface.
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        log = root / "logs" / "traffic" / "APRS 2M" / f"{old:%Y-%m-%d}.log"
        log.parent.mkdir(parents=True)
        log.write_text("# APRSBox traffic log: APRS 2M\n"
                       f"{old:%Y-%m-%d %H:%M:%S} RX        SP9ABC>APRS,WIDE1-1::SP9TKN   :old message{{3\n"
                       f"{old:%Y-%m-%d %H:%M:%S} TX        SP9ABC>APRS,SP9TKN*::SP9TKN   :old message{{3\n", encoding="utf-8")
        recent = analyze_station("SP9ABC", hours=1)
        longer = analyze_station("SP9ABC", hours=6)
    assert recent["bands"]["APRS 2M"]["own_unique"] == 0
    band = longer["bands"]["APRS 2M"]
    assert band["own_unique"] == 1 and band["msgs"][0]["text"] == "old message"


def test_findu_is_an_extra_that_may_be_missing() -> None:
    from app.desktop.station_analysis import analyze_station

    with station_data():
        frame(10, "SP9ABC>APRS,WIDE1-1:!5005.00N/01940.00E-QTH")
        with_findu = analyze_station("SP9ABC", hours=1, use_findu=True)  # offline in tests
        without = analyze_station("SP9ABC", hours=1, use_findu=False)
    assert with_findu["findu"]["ok"] is False and with_findu["findu"]["err"] == "OSError"
    assert without["findu"] == {"ok": False, "off": True}
    assert with_findu["bands"] == without["bands"]
    assert analyze_station("not a call")["error"] == "invalid_call"
