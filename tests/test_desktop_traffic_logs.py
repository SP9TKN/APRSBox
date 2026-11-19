"""Traffic monitor logs: continuous per-interface files and exports."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import desktop_support


@pytest.fixture()
def interfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    monkeypatch.setenv("APRSBOX_DB_PATH", str(tmp_path / "data" / "aprsbox.db"))
    monkeypatch.setenv("APRSBOX_LOG_DIR", str(tmp_path / "logs"))
    from app.db import init_db

    init_db()
    lora = desktop_support.seed_tcp_interface(name="LORA 70CM", band="70cm")
    direwolf = desktop_support.seed_tcp_interface(name="DIREWOLF 290R2")
    return lora, direwolf


def add_frame(interface_id: int, line: str, *, direction: str = "rx", command: str = "", created_at: datetime | None = None) -> None:
    from app.db import execute

    stamp = (created_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    execute(
        """
        INSERT INTO traffic_frames(source, source_kind, interface_id, direction, band, format, line, port, command, length, hex, created_at)
        VALUES ('Test', 'rf', ?, ?, '', 'TNC2', ?, '0', ?, ?, '', ?)
        """,
        (interface_id, direction, line, command, len(line), stamp),
    )


def test_continuous_log_reads_through_one_connection(interfaces: tuple[int, int], monkeypatch: pytest.MonkeyPatch) -> None:
    from app import db
    from app.desktop.traffic_logs import TrafficLogWriter, set_interface_logging

    lora, _direwolf = interfaces
    set_interface_logging(lora, True)
    add_frame(lora, "SP9TKN-1 > APRS:>logged")
    writer = TrafficLogWriter(poll_seconds=60)  # not started: run_once by hand
    opened: list[int] = []
    original = db.connect
    monkeypatch.setattr(db, "connect", lambda: opened.append(1) or original())
    assert writer.run_once() == 1
    assert len(opened) == 1  # every 2 s: the switch, the frames and the interface names together


def test_tnc2_text_and_direction_labels() -> None:
    from app.desktop.traffic_logs import direction_label, tnc2

    assert tnc2("SP9TKN-1 > APRS , SP9XBF-2,WIDE1*:=5005.67N/01940.55E> a > b , c") == (
        "SP9TKN-1>APRS,SP9XBF-2,WIDE1*:=5005.67N/01940.55E> a > b , c"
    )
    assert tnc2("no header here") == "no header here"
    assert direction_label({"direction": "rx"}) == "RX"
    assert direction_label({"direction": "tx", "command": "TX-PROXY 127.0.0.1"}) == "TX-REMOTE"


def test_continuous_log_follows_the_per_interface_switch(interfaces: tuple[int, int]) -> None:
    from app.desktop.traffic_logs import TrafficLogWriter, interface_log_dir, set_interface_logging

    lora, direwolf = interfaces
    add_frame(lora, "OLD > APRS:>before the program started")
    writer = TrafficLogWriter(poll_seconds=60)
    writer.start()
    try:
        set_interface_logging(lora, True)
        add_frame(lora, "SP9TKN-1 > APRS , SP9XBF-2,WIDE1*:=day one", created_at=datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc))
        add_frame(lora, "SP9QLJ-3 > APLOX1:=day two", created_at=datetime(2026, 10, 9, 0, 0, 1, tzinfo=timezone.utc))
        add_frame(direwolf, "SP9ABC > APRS:>2m frame, logging off")
        assert writer.run_once() == 2
        folder = interface_log_dir("LORA 70CM", lora)
        day_one = (folder / "2026-10-08.log").read_text(encoding="utf-8").splitlines()
        day_two = (folder / "2026-10-09.log").read_text(encoding="utf-8").splitlines()
        assert day_one[0].startswith("# APRSBox traffic log: LORA 70CM") and len(day_one) == 2
        assert day_one[1].split()[:3] == ["2026-10-08", "23:59:59", "RX"]
        assert day_one[1].endswith("SP9TKN-1>APRS,SP9XBF-2,WIDE1*:=day one")
        assert day_two[1].endswith("SP9QLJ-3>APLOX1:=day two")
        assert not interface_log_dir("DIREWOLF 290R2", direwolf).exists()
        assert "OLD" not in "".join(day_one + day_two)

        set_interface_logging(lora, False)
        add_frame(lora, "LATE > APRS:>logging switched off")
        assert writer.run_once() == 0
        set_interface_logging(direwolf, True)
        add_frame(direwolf, "SP9ABC > APRS:>now logged", direction="tx", created_at=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc))
    finally:
        writer.stop()  # flushes frames that arrived after the last poll
    lines = (interface_log_dir("DIREWOLF 290R2", direwolf) / "2026-10-09.log").read_text(encoding="utf-8").splitlines()
    assert lines[1].split()[2] == "TX" and lines[1].endswith("SP9ABC>APRS:>now logged")


def test_export_txt_and_csv_with_filters(interfaces: tuple[int, int], tmp_path: Path) -> None:
    from app.desktop.traffic_logs import CSV_HEADER, export_frames

    lora, direwolf = interfaces
    add_frame(lora, "SP9TKN-1 > APRS , WIDE1*:=semi;colon", created_at=datetime.now(timezone.utc) - timedelta(hours=3))
    add_frame(lora, "SP9QLJ-3 > APLOX1:=recent")
    add_frame(direwolf, "SP9ABC > APRS:>own tx", direction="tx")
    add_frame(direwolf, "SP9REM > APRS:>remote client", direction="tx", command="TX-PROXY 192.168.0.10")

    assert export_frames(tmp_path / "all.txt") == 4
    text = (tmp_path / "all.txt").read_text(encoding="utf-8")
    assert "[LORA 70CM]" in text and "[DIREWOLF 290R2]" in text and "SP9QLJ-3>APLOX1:=recent" in text
    assert export_frames(tmp_path / "lora.txt", interface_id=lora) == 2
    assert export_frames(tmp_path / "recent.txt", minutes=60) == 3
    assert export_frames(tmp_path / "tx.txt", direction="tx") == 1
    assert export_frames(tmp_path / "remote.txt", direction="tx-remote") == 1

    assert export_frames(tmp_path / "lora.csv", interface_id=lora, fmt="csv") == 2
    with (tmp_path / "lora.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    assert rows[0] == CSV_HEADER
    assert rows[1][1:8] == ["LORA 70CM", "RX", "rf", "SP9TKN-1", "APRS", "WIDE1*", "=semi;colon"]
    with pytest.raises(ValueError):
        export_frames(tmp_path / "bad.xml", fmt="xml")
