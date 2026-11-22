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


def _edges(result: dict) -> dict:
    return {(edge["layer"], edge["a"], edge["b"]): (edge["n"], edge["uncertain"], edge["direct"])
            for edge in result["route_map"]["edges"]}


def test_route_map_draws_who_heard_the_station_and_whose_frames_it_repeated(monkeypatch) -> None:
    from app.desktop import station_analysis

    real = station_analysis._known_stations

    def with_digis():
        positions, stations = real()
        positions.update({"SR9XSR": (50.02, 19.15), "SP9XDG": (50.26, 19.92), "SR9IGT": (50.0, 19.0)})
        return positions, stations

    monkeypatch.setattr(station_analysis, "_known_stations", with_digis)
    with station_data():
        frame(30, "SP9ABC>APRS,WIDE2-2:!5006.00N/01830.00E-QTH")
        frame(29.9, "SP9ABC>APRS,SR9XSR*,WIDE2-1:!5006.00N/01830.00E-QTH")
        frame(20, "SP9ABC>APRS,SR9XSR*,WIDE2-1:!5006.00N/01830.00E-QTH 2")
        frame(10, "SP9ABC>APRS,SP9XDG,WIDE1,WIDE2*:>through an unknown digipeater after SP9XDG")
        frame(15, "SP9XYZ>APRS,SP9ABC*,WIDE2-1:!5010.00N/01850.00E-x")  # SP9ABC repeated SP9XYZ
        frame(12, "SP9ABC>APRS,WIDE1-1,qAR,SR9IGT:!5006.00N/01830.00E-QTH", kind="aprsis")
        result = station_analysis.analyze_station("SP9ABC", hours=1)
    assert _edges(result) == {
        ("own", "SP9ABC", "SP9TKN"): (1, False, True),
        ("own", "SP9ABC", "SR9XSR"): (2, False, False),
        ("own", "SR9XSR", "SP9TKN"): (2, False, True),
        ("own", "SP9ABC", "SP9XDG"): (1, False, False),
        ("own", "SP9XDG", "SP9TKN"): (1, True, True),  # the unknown digipeater is bridged: dashed
        ("digi", "SP9XYZ", "SP9ABC"): (1, False, False),
        ("aprsis", "SP9ABC", "SR9IGT"): (1, False, False),
    }
    route_map = result["route_map"]
    assert route_map["focus"] == "SP9ABC" and route_map["own"] == "SP9TKN"
    assert abs(route_map["nodes"]["SP9ABC"]["lat"] - 50.1) < 1e-4  # from its own frames
    assert abs(route_map["nodes"]["SP9XYZ"]["lon"] - (18 + 50 / 60)) < 1e-4  # from the frame it repeated
    assert route_map["layers"] == {"network": 0, "digi": 1, "aprsis": 1, "own": 5}
    assert route_map["edges"][0]["layer"] == "digi"  # drawn first, the station's own routes on top


def test_route_map_of_the_own_station_shows_the_whole_network() -> None:
    from app.desktop.station_analysis import analyze_station

    with station_data() as root:
        frame(20, "SR9XSR>APRS,WIDE2-1:!5007.15N/01909.11E#digi")
        frame(19, "SP9XW>APRS,SR9XSR*,WIDE2-1:!5021.48N/01955.30E-x")
        frame(18, "SP9TKN>APRS,SR9XSR*:!5005.67N/01940.55E#me")  # our own frame heard back
        frame(17, "SP9XW>APRS,SP9TKN*,WIDE2-1:!5021.48N/01955.30E-x", direction="tx")  # we repeated it
        frame(16, "SR9XSR>APRS,WIDE1-1:}DL1ABC>APRS,TCPIP,SR9XSR*::SP9TKN   :hi")  # third-party: no route
        old = datetime.now(timezone.utc) - timedelta(hours=3)  # before the retention: the daily log
        log = root / "logs" / "traffic" / "APRS 2M" / f"{old:%Y-%m-%d}.log"
        log.parent.mkdir(parents=True)
        log.write_text("# APRSBox traffic log: APRS 2M\n"
                       f"{old:%Y-%m-%d %H:%M:%S} RX        SQ9AAA>APRS,SR9XSR*,WIDE2-1:!5000.00N/01900.00E-x\n"
                       f"{old:%Y-%m-%d %H:%M:%S} TX        SQ9AAA>APRS,SP9TKN*,WIDE2-1:!5000.00N/01900.00E-x\n",
                       encoding="utf-8")
        result = analyze_station("SP9TKN", hours=6)
        other = analyze_station("SP9XW", hours=6)
    edges = _edges(result)
    assert {key: value for key, value in edges.items() if key[0] == "network"} == {
        ("network", "SR9XSR", "SP9TKN"): (3, False, True),
        ("network", "SP9XW", "SR9XSR"): (1, False, False),
        ("network", "SQ9AAA", "SR9XSR"): (1, False, False),
    }
    assert edges[("own", "SP9TKN", "SR9XSR")] == (1, False, False)
    assert edges[("own", "SR9XSR", "SP9TKN")] == (1, False, True)
    assert edges[("digi", "SP9XW", "SP9TKN")] == (1, False, False)
    assert edges[("digi", "SQ9AAA", "SP9TKN")] == (1, False, False)
    bands = next(edge["bands"] for edge in result["route_map"]["edges"] if edge["layer"] == "network" and edge["a"] == "SR9XSR")
    assert bands == {"APRS 2M": 3}
    assert not any(key[0] == "network" for key in _edges(other))  # only the own station gets the network
