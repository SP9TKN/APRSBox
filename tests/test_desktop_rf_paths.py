"""RF path lines on the desktop map (app/desktop/rf_paths.py)."""

from __future__ import annotations

from app.desktop.rf_paths import (
    KIND_APRSIS,
    KIND_RF_DIGI,
    KIND_RF_DIRECT,
    build_segments,
    route_from_frame,
)

OWN = "SQ9TST"


def rf(line: str, heard_at: str = "2026-10-07T10:00:00+00:00"):
    return route_from_frame(line, source_kind="rf", own_callsign=OWN, heard_at=heard_at)


def test_direct_frame_has_no_hops() -> None:
    route = rf("SP9ABC>APRS,WIDE1-1,WIDE2-1:!5006.72N/01944.10E-")
    assert route is not None
    assert route.kind == KIND_RF_DIRECT and route.hops == [] and route.receiver == OWN


def test_digipeated_frame_lists_the_digipeater() -> None:
    route = rf("SP9QWE>APDR16,SR9XSR*,WIDE2-1:>status")
    assert route is not None
    assert route.kind == KIND_RF_DIGI and route.hops == ["SR9XSR"]


def test_generic_alias_without_callsign_is_an_unknown_hop() -> None:
    route = rf("SP9RTY>APRS,WIDE1*,WIDE2-1:>x")
    assert route is not None
    assert route.hops == [None]


def test_alias_after_inserted_callsign_is_not_a_second_hop() -> None:
    route = rf("SQ9PLK>APRS,SR9KFZ-2*,WIDE2*:>x")
    assert route is not None
    assert route.hops == ["SR9KFZ-2"]


def test_aprsis_frame_is_drawn_to_the_rf_igate() -> None:
    route = route_from_frame("SP9XIS>APRS,SR9XSR*,WIDE2-1,qAR,SP9XDG-10:>x", source_kind="aprsis", own_callsign=OWN)
    assert route is not None
    assert route.kind == KIND_APRSIS and route.receiver == "SP9XDG-10" and route.hops == ["SR9XSR"]


def test_internet_only_and_third_party_frames_are_skipped() -> None:
    assert route_from_frame("SP9NET>APRS,TCPIP*,qAC,T2POLAND:>x", source_kind="aprsis", own_callsign=OWN) is None
    assert rf("SP9XGW>APRS,WIDE2-1:}SP9X>APRS,TCPIP,SP9XGW*:>x") is None
    assert rf(f"{OWN}>APRS,WIDE1-1:>own frame") is None


def test_segments_follow_station_digi_receiver() -> None:
    positions = {"SP9QWE": (50.25, 20.10), "SR9XSR": (50.10, 19.80), OWN: (50.09, 19.67)}
    segments = build_segments([rf("SP9QWE>APDR16,SR9XSR*,WIDE2-1:>x")], positions)
    pairs = [(segment.start, segment.end, segment.kind, segment.uncertain) for segment in segments]
    assert ("SP9QWE", "SR9XSR", KIND_RF_DIGI, False) in pairs
    assert ("SR9XSR", OWN, KIND_RF_DIRECT, False) in pairs


def test_missing_digipeater_position_gives_one_uncertain_segment() -> None:
    positions = {"SP9QWE": (50.25, 20.10), OWN: (50.09, 19.67)}
    segments = build_segments([rf("SP9QWE>APDR16,SR9XSR*,WIDE2-1:>x")], positions)
    assert [(segment.start, segment.end, segment.uncertain) for segment in segments] == [("SP9QWE", OWN, True)]


def test_latest_route_per_station_wins_and_filters_apply() -> None:
    positions = {"SP9QWE": (50.25, 20.10), "SR9XSR": (50.10, 19.80), OWN: (50.09, 19.67), "SP9XDG-10": (50.1, 19.0)}
    older = rf("SP9QWE>APDR16,SR9XSR*,WIDE2-1:>x", heard_at="2026-10-07T09:00:00+00:00")
    newer = rf("SP9QWE>APDR16,WIDE1-1:>x", heard_at="2026-10-07T10:00:00+00:00")
    segments = build_segments([older, newer], positions)
    assert [(segment.start, segment.end, segment.kind) for segment in segments] == [("SP9QWE", OWN, KIND_RF_DIRECT)]
    gated = route_from_frame("SP9QWE>APRS,qAR,SP9XDG-10:>x", source_kind="aprsis", own_callsign=OWN, heard_at="2026-10-07T10:00:00+00:00")
    assert build_segments([gated], positions, include_aprsis=False) == []
    assert [segment.kind for segment in build_segments([gated], positions)] == [KIND_APRSIS]


def test_recent_routes_window_is_in_seconds(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from app.db import execute, init_db
    from app.desktop.rf_paths import load_recent_routes

    monkeypatch.setenv("APRSBOX_DB_PATH", str(tmp_path / "aprsbox.db"))
    init_db()
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    for seconds_ago, call in ((0, "SP9AAA"), (20, "SP9BBB")):
        execute(
            """
            INSERT INTO traffic_frames(source, source_kind, interface_id, direction, band, format, line, port, command, length, hex, created_at)
            VALUES ('test', 'rf', 1, 'rx', '2m', 'TNC2', ?, '0', '', 20, '', ?)
            """,
            (f"{call}>APRS,WIDE1-1:>window test", (now - timedelta(seconds=seconds_ago)).isoformat()),
        )
    assert [route.station for route in load_recent_routes(15, OWN, now=now)] == ["SP9AAA"]
    assert sorted(route.station for route in load_recent_routes(30, OWN, now=now)) == ["SP9AAA", "SP9BBB"]


def test_frame_reception_tells_who_received_a_frame() -> None:
    from app.desktop.rf_paths import VIA_IGATE, VIA_INTERNET, VIA_RF, frame_reception

    rf_digi = frame_reception("SQ9XAA-7>APRS,SP9XDG*,WIDE2*:!5005.00N/01940.00E>", source_kind="rf", own_callsign=OWN)
    assert (rf_digi.via, rf_digi.receiver, rf_digi.hops, rf_digi.q_construct) == (VIA_RF, OWN, ["SP9XDG"], "")
    direct = frame_reception("SP9ABC>APRS,WIDE1-1,WIDE2-1:!5005.00N/01940.00E-", source_kind="rf", own_callsign=OWN)
    assert (direct.via, direct.hops) == (VIA_RF, [])
    gated = frame_reception("SR9XNW>APMI03,SP9XDG*,WIDE2-1,qAR,SP9XDG-10:=4927.19NL02059.53E#",
                            source_kind="aprsis", own_callsign=OWN)
    assert (gated.via, gated.receiver, gated.q_construct, gated.hops) == (VIA_IGATE, "SP9XDG-10", "qAR", ["SP9XDG"])
    internet = frame_reception("SP9XWP-10>APDW17,TCPIP*,qAC,T2DENMARK:!5016.20NL02005.40E&", source_kind="aprsis",
                               own_callsign=OWN)
    assert (internet.via, internet.receiver, internet.q_construct, internet.hops) == (VIA_INTERNET, "T2DENMARK", "qAC", [])
    third_party = frame_reception("SP9XBF-2>APLOX1:}SP9TWP-2>APLRG1,TCPIP,SP9XBF-2*:!L5%95SCi]#", source_kind="rf",
                                  own_callsign=OWN)
    assert (third_party.via, third_party.gateway) == (VIA_RF, "SP9XBF-2")


def test_object_owner_is_the_station_that_sent_the_object() -> None:
    from app.desktop.rf_paths import object_owner

    assert object_owner("SR9XSR>APNU19,WIDE2-1:;MEETING  *071200z5005.70N/01939.90EEClub meeting") == "SR9XSR"
    assert object_owner("SP9ABC-0>APRS:)AID #2!4851.50N/07201.75WA") == "SP9ABC"
    assert object_owner("SP9XBF-2>APLOX1:}SR9XX>APRS,TCPIP,SP9XBF-2*:;RPT      *111111z4851.50N/07201.75Wr") == "SR9XX"
    assert object_owner("SP9ABC>APRS,WIDE1-1:!5006.72N/01944.10E-") == ""


def test_reception_segments_lead_from_the_track_point_to_the_receiver() -> None:
    from app.desktop.rf_paths import KIND_INTERNET, VIA_INTERNET, reception_segments

    dot = (50.20, 19.90)
    positions = {OWN: (50.09, 19.67), "SP9XDG": (50.15, 20.05), "SR9XNW": (49.45, 20.99), "SP9XDG-10": (49.90, 20.20),
                 "SP9XBF-2": (50.00, 19.80)}

    def legs(line: str, kind: str = "rf", known: dict | None = None):
        segments, reception, missing = reception_segments(line, source_kind=kind, own_callsign=OWN, station="SQ9XAA-7",
                                                          start=dot, positions=positions if known is None else known)
        return [(s.start, s.end, s.start_pos, s.end_pos, s.kind, s.uncertain) for s in segments], reception, missing

    digi, _reception, missing = legs("SQ9XAA-7>APRS,SP9XDG*,WIDE2*:!5012.00N/01954.00E>")
    assert digi == [("SQ9XAA-7", "SP9XDG", dot, positions["SP9XDG"], KIND_RF_DIGI, False),
                    ("SP9XDG", OWN, positions["SP9XDG"], positions[OWN], KIND_RF_DIRECT, False)] and missing == []
    direct, _reception, _missing = legs("SQ9XAA-7>APRS,WIDE1-1,WIDE2-1:>x")
    assert direct == [("SQ9XAA-7", OWN, dot, positions[OWN], KIND_RF_DIRECT, False)]
    unknown, _reception, _missing = legs("SQ9XAA-7>APRS,WIDE1*,WIDE2-1:>x")
    assert unknown == [("SQ9XAA-7", OWN, dot, positions[OWN], KIND_RF_DIRECT, True)]
    gated, _reception, _missing = legs("SQ9XAA-7>APRS,SR9XNW*,WIDE2-1,qAR,SP9XDG-10:>x", "aprsis")
    assert [(start, end, kind) for start, end, _a, _b, kind, _u in gated] == [
        ("SQ9XAA-7", "SR9XNW", KIND_APRSIS), ("SR9XNW", "SP9XDG-10", KIND_APRSIS)]
    # An iGate that is not on the map: the way is drawn as far as it is known.
    partial, _reception, missing = legs("SQ9XAA-7>APRS,SR9XNW*,WIDE2-1,qAR,SP9XYZ-10:>x", "aprsis")
    assert [(start, end) for start, end, *_rest in partial] == [("SQ9XAA-7", "SR9XNW")] and missing == ["SP9XYZ-10"]
    internet, reception, missing = legs("SQ9XAA-7>APRS,TCPIP*,qAC,T2POLAND:>x", "aprsis")
    assert internet == [] and reception.via == VIA_INTERNET and missing == []
    # Third-party frame: over the Internet to the gateway, then on RF to the own station.
    third = "SP9XBF-2>APLOX1:}SQ9XAA-7>APLRG1,TCPIP,SP9XBF-2*:!L5%95SCi]#"
    gateway, _reception, _missing = legs(third)
    assert [(start, end, kind) for start, end, _a, _b, kind, _u in gateway] == [
        ("SQ9XAA-7", "SP9XBF-2", KIND_INTERNET), ("SP9XBF-2", OWN, KIND_RF_DIRECT)]
    no_gateway, _reception, missing = legs(third, known={OWN: positions[OWN]})
    assert no_gateway == [] and missing == ["SP9XBF-2"]
