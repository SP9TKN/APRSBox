"""CWOP weather stations: a receive-only feed from cwop.aprs.net, switched on in Settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import desktop_support

FW0001 = ("FW0001>APN000,TCPXX*,qAX,CWOP-7:@091511z5007.88N/01953.08E_179/001g006t056r000p000P003b10147h63"
          "L027eMB54")


@pytest.fixture()
def station_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APRSBOX_DB_PATH", str(tmp_path / "data" / "aprsbox.db"))
    from app.db import init_db

    init_db()
    desktop_support.seed_station_settings()


def aprsis_interface(server_filter: str) -> None:
    from app.services.content import safe_create_section_row

    success, error = safe_create_section_row(
        "modems", {"name": "APRS-IS", "band": "", "modem_type": "APRSIS", "device_path": server_filter, "enabled": "1"})
    assert success, error


def test_switched_on_in_settings_with_the_radius_of_the_aprsis_filter(station_db: None) -> None:
    from app.db import execute
    from app.services.cwop import DEFAULT_CWOP_RANGE_KM, get_cwop_settings, save_cwop_settings

    assert get_cwop_settings() == {"enabled": False, "range_km": DEFAULT_CWOP_RANGE_KM}  # off, no APRS-IS yet
    save_cwop_settings(enabled=True)
    aprsis_interface("m/30")
    assert get_cwop_settings() == {"enabled": True, "range_km": 30}
    execute("UPDATE modems SET device_path = 'r/50.1/18.9/120' WHERE modem_type = 'APRSIS'")
    assert get_cwop_settings()["range_km"] == 120


def test_receiver_logs_in_read_only_with_a_range_around_my_station(station_db: None) -> None:
    from app.services.cwop import CWOP_PORT, CWOP_SERVER, CwopReceiverService, save_cwop_settings

    service = CwopReceiverService()
    interface, config = service._read_desired_state()
    assert interface is None  # off: no connection
    save_cwop_settings(enabled=True)
    aprsis_interface("m/50")
    interface, config = service._read_desired_state()
    lat, lon = desktop_support.OWN_LATITUDE, desktop_support.OWN_LONGITUDE
    assert interface is not None and interface["effective_filter"] == f"r/{lat:.4f}/{lon:.4f}/50"
    assert (config["server"], config["port"], config["passcode"]) == (CWOP_SERVER, CWOP_PORT, "-1")
    assert config["login"] == desktop_support.OWN_CALLSIGN
    assert service._rx_signature(interface) == (0, interface["effective_filter"])  # stable: no reconnect loop


def test_cwop_frames_reach_the_map_but_never_the_routing(station_db: None) -> None:
    from app.services.cwop import CwopReceiverService, save_cwop_settings
    from app.services.map_service import get_map_station_payload

    received: list[tuple[str, dict[str, Any]]] = []
    routed: list[str] = []

    def processor(line: str, **kwargs: Any) -> bool:
        received.append((line, kwargs))
        from app.services.traffic import process_normalized_tnc2_rx

        return process_normalized_tnc2_rx(line, **kwargs)

    service = CwopReceiverService(rx_processor=processor)
    service.set_frame_consumer(lambda line, **kwargs: routed.append(line))  # the runtime never does; even so
    save_cwop_settings(enabled=True)
    service._desired_rx_interface, _config = service._read_desired_state()
    assert service._process_server_line(FW0001)
    assert routed == []
    line, kwargs = received[0]
    assert kwargs["source"] == "CWOP · cwop.aprs.net" and kwargs["source_kind"] == "aprsis"
    assert kwargs["source_interface_id"] is None
    stations = {item["display_callsign"]: item for item in get_map_station_payload()["stations"]}
    assert "FW0001" in stations and stations["FW0001"]["symbol_code"] == "_"  # a weather station


def test_status_is_kept_apart_from_the_aprsis_uplink(station_db: None) -> None:
    from app.services.aprsis import get_aprsis_runtime_status
    from app.services.cwop import CwopReceiverService, get_cwop_status

    before = get_aprsis_runtime_status()
    service = CwopReceiverService()
    service._persist_status(status="connected", status_detail="Connected to cwop.aprs.net:14580 as SQ9TST.")
    assert get_cwop_status()["status"] == "connected"
    assert get_aprsis_runtime_status()["status"] == before["status"]
    first = get_cwop_status()["at"]
    service._persist_status(status="connected", status_detail="Connected to cwop.aprs.net:14580 as SQ9TST.")
    assert get_cwop_status()["at"] == first  # unchanged state: not written again


def test_only_frames_inside_the_radius_get_in_whatever_the_server_sends(station_db: None) -> None:
    from app.services.cwop import CwopReceiverService, save_cwop_settings

    received: list[str] = []
    service = CwopReceiverService(rx_processor=lambda line, **kwargs: received.append(line) or True)
    save_cwop_settings(enabled=True)
    service._desired_rx_interface, _config = service._read_desired_state()
    far = "EW3975>APRS,TCPXX*,qAX,CWOP-4:@110553z3750.43N/12201.67W_171/000g000t057"  # California
    assert not service._process_server_line(far)
    assert service._process_server_line(FW0001)  # 15 km away
    assert received == [FW0001]


def test_a_server_ignoring_the_filter_is_left_for_another(station_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import socket

    from app.services import cwop
    from app.services.cwop import CwopReceiverService

    service = CwopReceiverService()

    class Writer:
        def get_extra_info(self, name: str) -> Any:
            return ("143.244.144.248", 14580) if name == "peername" else None

    service._writer = Writer()  # type: ignore[assignment]
    service._process_server_line("# aprsc 2.1.21-gbfc2090\r\n")  # an ordinary banner is fine
    with pytest.raises(ValueError):  # the reader loop disconnects on it
        service._process_server_line("# No user-specified filters on this port\r\n")
    assert "143.244.144.248" in service._blocked_addresses

    tried: list[str] = []

    async def fake_getaddrinfo(host: str, port: int, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
                for address in ("143.244.144.248", "44.155.254.4", "85.188.1.27")]

    async def fake_open_connection(address: str, port: int) -> tuple[str, str]:
        tried.append(address)
        return "reader", "writer"

    monkeypatch.setattr(cwop.asyncio, "open_connection", fake_open_connection)

    async def run() -> Any:
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
        return await service._open_connection(cwop.CWOP_SERVER, cwop.CWOP_PORT)

    for _ in range(10):
        assert asyncio.run(run()) == ("reader", "writer")
    assert tried and "143.244.144.248" not in tried  # the full-feed server is skipped for 6 hours
