"""Offline fixtures for the desktop GUI tests and screenshot tooling.

Frames are fed through ``app.services.traffic.process_normalized_tnc2_rx`` -
the same entry point KISS and APRS-IS use - so stations, tracks, statistics,
alerts and messages are built by the real backend.  Nothing is transmitted.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OWN_CALLSIGN = "SQ9TST"
OWN_LATITUDE = 50.0945
OWN_LONGITUDE = 19.6758


def isolated_environment(root: Path | None = None) -> Path:
    """Point every APRSBOX_* runtime path at a fresh temporary folder."""
    base = Path(root or tempfile.mkdtemp(prefix="aprsbox-desktop-"))
    os.environ["APRSBOX_DB_PATH"] = str(base / "data" / "aprsbox.db")
    os.environ["APRSBOX_LOG_DIR"] = str(base / "logs")
    os.environ["APRSBOX_CONFIG_DIR"] = str(base / "config")
    os.environ["APRSBOX_BACKUPS_DIR"] = str(base / "backups")
    os.environ["APRSBOX_IDLE_SLOWDOWN"] = "0"  # an idle test machine must not slow the pages down
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if windows_fonts.is_dir():
        # The offscreen platform only sees fonts from QT_QPA_FONTDIR.
        os.environ.setdefault("QT_QPA_FONTDIR", str(windows_fonts))
    return base


def aprs_lat(value: float) -> str:
    hemisphere = "N" if value >= 0 else "S"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees:02d}{minutes:05.2f}{hemisphere}"


def aprs_lon(value: float) -> str:
    hemisphere = "E" if value >= 0 else "W"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees:03d}{minutes:05.2f}{hemisphere}"


def position(latitude: float, longitude: float, table: str = "/", code: str = ">", comment: str = "") -> str:
    return f"!{aprs_lat(latitude)}{table}{aprs_lon(longitude)}{code}{comment}"


DIGIS = {
    "SR9XSR": (50.1790, 19.9010, "Digi Testowo PHG5360"),
    "SR9KFZ-2": (49.6160, 20.0430, "Digi Szczyrk"),
    "SP9XDG-10": (50.0402, 19.5300, "IGate / digi"),
}


def seed_station_settings(*, callsign: str = OWN_CALLSIGN, interface_id: int | None = None, tx_enabled: bool = False) -> None:
    from app.services.content import update_station_settings

    update_station_settings(
        {
            "callsign": callsign,
            "ssid": "",
            "beacon_interface_id": str(interface_id or ""),
            "beacon_comment": "APRSBox desktop test",
            "beacon_interval_minutes": "30",
            "beacon_path": "WIDE1-1,WIDE2-1",
            "status_text": "Testing",
            "status_interval_minutes": "60",
            "latitude": f"{OWN_LATITUDE}",
            "longitude": f"{OWN_LONGITUDE}",
            "symbol_table": "/",
            "symbol_code": "#",
            "default_units": "metric",
            "tx_enabled": "1" if tx_enabled else "",
        }
    )


def seed_tcp_interface(*, name: str = "Test TNC", host_port: str = "127.0.0.1:1", enabled: bool = False, band: str = "2m") -> int:
    from app.db import execute, fetch_one

    execute(
        """
        INSERT INTO modems(name, modem_type, band, device_path, baud_rate, enabled, notes, created_at, updated_at)
        VALUES (?, 'TCP', ?, ?, NULL, ?, '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """,
        (name, band, host_port, 1 if enabled else 0),
    )
    row = fetch_one("SELECT id FROM modems WHERE name = ? ORDER BY id DESC LIMIT 1", (name,))
    assert row is not None
    return int(row["id"])


def demo_frames(now: datetime | None = None, *, with_alerts: bool = False) -> list[dict[str, Any]]:
    """Realistic RX traffic around the test QTH for the last ~40 minutes."""
    now = now or datetime.now(timezone.utc)
    frames: list[dict[str, Any]] = []

    def add(minutes_ago: float, line: str, *, kind: str = "rf") -> None:
        frames.append(
            {
                "line": line,
                "source_kind": kind,
                "timestamp": (now - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat(),
            }
        )

    for index, (call, (lat, lon, comment)) in enumerate(DIGIS.items()):
        add(38 - index, f"{call}>APNU19,WIDE2-1:{position(lat, lon, '/', '#', comment)}")
    # Fixed stations heard directly and via digipeaters.
    add(35, f"SP9ABC>APRS,WIDE1-1,WIDE2-1:{position(50.1120, 19.7350, '/', '-', 'QTH Zabrze')}")
    add(30, f"SP9QWE>APDR16,SR9XSR*,WIDE2-1:{position(50.2510, 20.1020, '/', '-', 'Miasteczko')}")
    add(28, f"SQ9PLK>APRS,SR9KFZ-2*,WIDE2*:{position(49.4800, 20.1900, '/', '-', 'Zywiec')}")
    add(26, f"SP9RTY>APRS,WIDE1*,WIDE2-1:{position(49.9500, 19.4100, '/', '-', 'unknown hop')}")
    # Weather station.
    add(20, f"SP9XWX-13>APRS,SR9XSR*,WIDE2-1:@071230z{aprs_lat(50.1300)}/{aprs_lon(19.6100)}_270/004g008t050r000p000P000h85b10132WX Testowo")
    # Mobile with a track.
    for step in range(6):
        lat = 50.20 + step * 0.012
        lon = 18.55 + step * 0.025
        add(24 - step * 3, f"SP9MOB-9>APDR16,WIDE1-1,WIDE2-1:{position(lat, lon, '/', '>', 'mobile test')}")
    # Object.
    add(18, f"SR9XSR>APNU19,WIDE2-1:;MEETING  *071200z{aprs_lat(50.0950)}/{aprs_lon(19.6650)}EClub meeting")
    # APRS-IS traffic heard on RF by an IGate.
    add(15, f"SP9XIS>APRS,SR9XSR*,WIDE2-1,qAR,SP9XDG-10:{position(50.3000, 19.3000, '/', '-', 'via IGate')}", kind="aprsis")
    add(12, f"SP9NET>APRS,TCPIP*,qAC,T2POLAND:{position(49.8600, 20.9400, '/', '-', 'Internet only')}", kind="aprsis")
    # Message addressed to the own station.
    add(6, f"SP9ABC>APRS,WIDE1-1::{OWN_CALLSIGN:<9}:Czesc, test wiadomosci{{12")
    # Status.
    add(5, "SP9QWE>APDR16,SR9XSR*,WIDE2-1:>On the air")
    if with_alerts:
        # Emergency position and a CAWF group warning (alarm group PL-WARN, see seed_demo_alarms).
        add(9, f"SP9EMG-9>APRS,WIDE1-1:!{aprs_lat(50.0700)}\\{aprs_lon(19.7100)}$!EMERGENCY!Need help")
        expires = (now + timedelta(hours=6)).strftime("%d%H%Mz")
        add(4, f"PLWXSR>APRS,TCPIP*::PL-WARN  :{expires},TSTORM3,@A7F3,1/1,1465{{91AC2", kind="aprsis")
    return frames


def seed_demo_alarms() -> None:
    """Enable alarm reception for the PL-WARN group used by the demo warning frame."""
    from app.services.alarm_groups import (
        get_aprs_alarm_category_thresholds,
        save_aprs_alarm_category_thresholds,
        save_aprs_alarm_enabled,
        save_aprs_alarm_groups,
    )

    save_aprs_alarm_enabled(True)
    save_aprs_alarm_groups("PL-WARN")
    thresholds = get_aprs_alarm_category_thresholds()
    for values in thresholds.values():
        values["alerts"] = 1
    save_aprs_alarm_category_thresholds(thresholds)


def seed_demo_traffic(*, interface_id: int | None = None, now: datetime | None = None,
                      frames: list[dict[str, Any]] | None = None, with_alerts: bool = False) -> int:
    from app.services.traffic import process_normalized_tnc2_rx

    count = 0
    for frame in frames or demo_frames(now, with_alerts=with_alerts):
        kind = frame.get("source_kind") or "rf"
        accepted = process_normalized_tnc2_rx(
            frame["line"],
            source="APRS-IS" if kind == "aprsis" else "Test TNC",
            source_kind=kind,
            source_interface_id=None if kind == "aprsis" else interface_id,
            band="" if kind == "aprsis" else "2m",
            timestamp=frame["timestamp"],
        )
        count += 1 if accepted else 0
    return count


def seed_demo_content() -> None:
    """One object, one item and two bulletins (disabled, so nothing is scheduled)."""
    from app.services.content import safe_create_section_row

    common = {"interval_minutes": "30", "activation_mode": "manual", "path": "", "is_enabled": None}
    results = [
        safe_create_section_row(
            "objects",
            {**common, "name": "MEETING", "lifetime": "temporary", "state": "live", "latitude": "50.0950",
             "longitude": "19.6650", "symbol_table": "/", "symbol_code": "r", "symbol_overlay": "",
             "comment": "Club meeting 19:00"},
        ),
        safe_create_section_row(
            "items",
            {**common, "name": "AID#2", "state": "live", "latitude": "50.1000", "longitude": "19.7000",
             "symbol_table": "/", "symbol_code": "+", "symbol_overlay": "", "comment": "First aid"},
        ),
        safe_create_section_row(
            "bulletins",
            {**common, "message_kind": "bulletin", "bulletin_code": "0", "group_name": "",
             "message_text": "Net starts at 19:30 UTC"},
        ),
        safe_create_section_row(
            "bulletins",
            {**common, "message_kind": "group_bulletin", "bulletin_code": "1", "group_name": "WX",
             "activation_mode": "recurring", "active_from_utc": "2026-01-05 18:00", "active_until_utc": "",
             "recurrence_duration_minutes": "120", "recurrence_interval_value": "1", "recurrence_interval_unit": "week",
             "message_text": "Weekly WX net"},
        ),
    ]
    for ok, error in results:
        assert ok, error


def seed_demo_flows(interface_name: str = "Test TNC") -> list[int]:
    """An RF digipeater flow and a log-only flow on the demo interface (disabled)."""
    from app.services.digi_flows import safe_create_digi_flow

    flows = [
        {
            "name": "WIDE1 fill-in",
            "description": "Fill-in digipeater for WIDE1-1",
            "source_selector": f"receiver_rf::{interface_name}",
            "target_selector": f"tx_rf::{interface_name}",
            "enabled": 0,
            "steps": [
                {"step_type": "receiver_rf", "title": "Receiver RF", "enabled": 1, "config": {"rf_port": interface_name}},
                {"step_type": "filter_dupe", "title": "", "enabled": 1, "config": {"window_sec": 5}},
                {"step_type": "filter_callsign", "title": "", "enabled": 1, "config": {"mode": "deny", "callsigns": ["N0CALL*"]}},
                {"step_type": "filter_path", "title": "", "enabled": 1,
                 "config": {"mode": "allow", "trace_paths": ["WIDE1-1"], "no_trace_paths": []}},
                {"step_type": "tx_rf", "title": "TX RF", "enabled": 1, "config": {"rf_target": interface_name, "rf_path": ""}},
            ],
        },
        {
            "name": "Log weather",
            "description": "",
            "source_selector": f"receiver_rf::{interface_name}",
            "target_selector": "action_log::log-only",
            "enabled": 0,
            "steps": [
                {"step_type": "receiver_rf", "title": "Receiver RF", "enabled": 1, "config": {"rf_port": interface_name}},
                {"step_type": "filter_packet_type", "title": "", "enabled": 1, "config": {"mode": "allow", "packet_types": ["weather"]}},
                {"step_type": "action_log", "title": "Black Hole", "enabled": 1, "config": {"log_tag": "log-only", "note": ""}},
            ],
        },
    ]
    created: list[int] = []
    for flow in flows:
        source_kind, source_ref = flow["source_selector"].split("::", 1)
        target_kind, target_ref = flow["target_selector"].split("::", 1)
        flow_id, error = safe_create_digi_flow({**flow, "source_kind": source_kind, "source_ref": source_ref,
                                                "target_kind": target_kind, "target_ref": target_ref})
        assert flow_id is not None, error
        created.append(flow_id)
    return created


def seed_demo_database(*, with_traffic: bool = True, with_alerts: bool = False) -> dict[str, Any]:
    from app.db import init_db, set_app_setting

    init_db()
    set_app_setting("app_language", "pl")
    interface_id = seed_tcp_interface()
    seed_station_settings(interface_id=interface_id)
    seed_demo_content()
    seed_demo_flows()
    if with_alerts:
        seed_demo_alarms()
    accepted = seed_demo_traffic(interface_id=interface_id, with_alerts=with_alerts) if with_traffic else 0
    if with_traffic:
        # The runtime's aggregator fills the statistics buckets; run one pass here.
        from app.services.radio_activity import run_radio_activity_aggregation

        run_radio_activity_aggregation(safety_delay_seconds=0)
    return {"interface_id": interface_id, "accepted": accepted}
