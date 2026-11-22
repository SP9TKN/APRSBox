"""The RF traffic channel of Messages: every message frame heard or sent on RF, every copy, kept for good."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from app.db import execute, fetch_all, init_db
from app.services.rf_message_log import (
    ORIGIN_APRSIS_TO_RF,
    ORIGIN_DIGI,
    ORIGIN_OWN,
    ORIGIN_RX,
    backfill_rf_message_log,
    list_rf_message_log,
    parse_rf_message,
)


@contextlib.contextmanager
def temporary_database():
    with tempfile.TemporaryDirectory() as temp_dir:
        previous = os.environ.get("APRSBOX_DB_PATH")
        os.environ["APRSBOX_DB_PATH"] = str(Path(temp_dir) / "aprsbox-test.db")
        try:
            init_db()
            yield
        finally:
            if previous is None:
                os.environ.pop("APRSBOX_DB_PATH", None)
            else:
                os.environ["APRSBOX_DB_PATH"] = previous


def own_station(callsign: str = "SP9TKN") -> None:
    import desktop_support

    desktop_support.seed_station_settings(callsign=callsign)


def logged() -> list[dict]:
    return [dict(row) for row in fetch_all(
        "SELECT direction, origin, band, interface_name, sender, addressee, message_text, message_number, kind, path, relay "
        "FROM rf_message_log ORDER BY id")]


def test_message_frames_are_recognised_with_their_kind() -> None:
    message = parse_rf_message("SQ9XID-11 > APLOX1 , SP9XDG-2*,WIDE1*::SP9TKN   :hello there{12")
    assert message == {"sender": "SQ9XID-11", "addressee": "SP9TKN", "text": "hello there", "number": "12",
                       "kind": "message", "path": "SP9XDG-2*,WIDE1*", "relay": ""}
    assert parse_rf_message("SQ9XC>APMI03,OK0XBD-1*::SQ9XC    :BITS.11111111,Telemetria")["kind"] == "telemetry"
    assert parse_rf_message("SP9ABC>APRS::SQ9XJ    :ack13")["kind"] == "ack"
    assert parse_rf_message("SP9ABC>APRS::SQ9XJ    :rej13")["kind"] == "rej"
    assert parse_rf_message("SP9ABC>APRS::BLN1     :Net at 19:30")["kind"] == "bulletin"
    assert parse_rf_message("SP9ABC>APRS::SP9TKN   :?APRSD")["kind"] == "query"
    # An internet message an iGate puts on RF: the inner sender and addressee, the RF path, the iGate.
    relayed = parse_rf_message("SR6XNW>APDR15,SP9XDG*,WIDE2*:}DL9XGR>APK005,TCPIP,SR6XNW*::OK0XV-5  :Tnx{44")
    assert (relayed["sender"], relayed["addressee"], relayed["text"], relayed["path"], relayed["relay"]) == \
        ("DL9XGR", "OK0XV-5", "Tnx", "SP9XDG*,WIDE2*", "SR6XNW")
    assert parse_rf_message("SP9ABC>APRS,WIDE1-1:!5005.67N/01940.55E-QTH") is None
    assert parse_rf_message("not a frame") is None


def test_every_heard_rf_copy_is_logged_and_aprs_is_is_not() -> None:
    from app.services.traffic import process_normalized_tnc2_rx

    with temporary_database():
        own_station()
        line = "SP9ABC>APRS,WIDE1-1::SP9XYZ   :Czesc{7"
        for band, source in (("2m", "APRS 2M"), ("70cm", "LORA 70CM"), ("2m", "APRS 2M")):
            process_normalized_tnc2_rx(line, source=source, source_kind="rf", band=band, timestamp="2026-11-22T10:00:00+00:00")
        process_normalized_tnc2_rx("SP9QQQ>APRS,TCPIP*::SP9XYZ   :via internet", source="APRS-IS", source_kind="aprsis",
                                   timestamp="2026-11-22T10:01:00+00:00")
        process_normalized_tnc2_rx("SQ9XC>APMI03::SQ9XC    :PARM.Temp", source="APRS 2M", source_kind="rf", band="2m",
                                   timestamp="2026-11-22T10:02:00+00:00")
        rows = logged()
    assert [(row["band"], row["interface_name"], row["origin"]) for row in rows] == [
        ("2m", "APRS 2M", ORIGIN_RX), ("70cm", "LORA 70CM", ORIGIN_RX), ("2m", "APRS 2M", ORIGIN_RX), ("2m", "APRS 2M", ORIGIN_RX)]
    assert rows[0]["sender"] == "SP9ABC" and rows[0]["message_text"] == "Czesc" and rows[0]["message_number"] == "7"
    assert rows[-1]["kind"] == "telemetry"  # the user wants telemetry and ACKs in the channel too


def test_sent_rf_frames_are_logged_with_who_sent_them() -> None:
    from app.services.outbound import persist_outbound_frame

    with temporary_database():
        own_station()
        persist_outbound_frame(source="APRS 2M", band="2m", line="SP9TKN>APBOX0,WIDE2-1::SQ9XJ    :hi{13")
        persist_outbound_frame(source="LORA 70CM", band="70cm", line="SP9TKN>APBOX0,WIDE2-1::SQ9XJ    :hi{13")
        persist_outbound_frame(source="APRS 2M", band="2m", line="SP9ABC>APRS,SP9TKN*::SQ9XJ    :digi{4")
        persist_outbound_frame(source="APRS 2M", band="2m", line="SP9QQQ>APRS,TCPIP,SP9TKN*::SP9ZZZ   :from IS{5",
                               source_kind="aprsis_to_rf")
        persist_outbound_frame(source="APRS 2M", band="2m", line="SP9TKN>APBOX0::SQ9XJ    :skipped{14", command="TX-SKIP")
        persist_outbound_frame(source="APRS-IS", line="SP9TKN>APBOX0,TCPIP*::SQ9XJ    :hi{13", source_kind="aprsis")
        persist_outbound_frame(source="APRS 2M", band="2m", line="SP9TKN>APBOX0,WIDE2-1:=5005.67N/01940.55E-beacon")
        rows = logged()
    assert [(row["direction"], row["origin"], row["band"]) for row in rows] == [
        ("tx", ORIGIN_OWN, "2m"), ("tx", ORIGIN_OWN, "70cm"), ("tx", ORIGIN_DIGI, "2m"), ("tx", ORIGIN_APRSIS_TO_RF, "2m")]


def test_the_traffic_log_fills_the_channel_once() -> None:
    with temporary_database():
        own_station()
        for direction, kind, line in (
            ("rx", "rf", "SP9ABC>APRS::SP9XYZ   :old one{1"),
            ("rx", "aprsis", "SP9ABC>APRS,TCPIP*::SP9XYZ   :internet"),
            ("tx", "rf", "SP9TKN>APBOX0::SP9XYZ   :mine{2"),
            ("rx", "rf", "SP9ABC>APRS:>status"),
        ):
            execute(
                "INSERT INTO traffic_frames(source, source_kind, interface_id, direction, band, format, line, port, command, "
                "length, hex, created_at) VALUES ('APRS 2M', ?, NULL, ?, '2m', 'TNC2', ?, '0', 'TX', 10, '', '2026-11-22T09:00:00+00:00')",
                (kind, direction, line),
            )
        assert backfill_rf_message_log() == 2
        assert backfill_rf_message_log() == 0  # once only
        listing = list_rf_message_log(limit=1)
    assert listing["total"] == 2
    assert [(item["direction"], item["origin"], item["text"]) for item in listing["items"]] == [("tx", ORIGIN_OWN, "mine")]
