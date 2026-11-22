"""Another station's receive page (SP9XDG's APRS ANALIZER /api/stats) as a listener for the traffic analyzer."""

from __future__ import annotations

import time

import pytest

from test_station_analysis import frame, station_data


def stats(*rows: tuple[str, float, str, str, str]) -> dict:
    payload = {"lora": {"rx": []}, "wx": {"rx": []}, "gateways": []}
    for channel, t, call, path, wide in rows:
        payload[channel]["rx"].append({"t": t, "call": call, "path": path, "wide": wide, "dev": "Mic-E", "known": True})
    return payload


def test_the_page_address_is_checked_and_named_after_its_host() -> None:
    from app.desktop.remote_receivers import normalize_url, receiver_name

    assert normalize_url(" sp9xdg.example.net:8080/ ") == "http://sp9xdg.example.net:8080"
    assert normalize_url("") == ""
    assert receiver_name("http://sp9xdg.example.net:8080") == "SP9XDG"
    with pytest.raises(ValueError):
        normalize_url("ftp://sp9xdg.example.net")


def test_polling_keeps_every_row_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop import remote_receivers

    now = time.time()
    payload = stats(("wx", now - 60, "SP9ABC", "RF", "WIDE1-1"), ("lora", now - 30, "SP9ABC", "SP9XDG-2", "WIDE1*"))
    with station_data():
        assert remote_receivers.poll_once() == 0  # no address: off, nothing fetched
        remote_receivers.set_remote_url("http://sp9xdg.example.net:8080")
        monkeypatch.setattr(remote_receivers, "fetch_stats", lambda url: payload)
        assert remote_receivers.poll_once() == 2
        assert remote_receivers.poll_once() == 0  # the same list a minute later
        rows = remote_receivers.heard_rows("SP9ABC", since=now - 3600)
        monkeypatch.setattr(remote_receivers, "fetch_stats", lambda url: {"busy": True})
        with pytest.raises(RuntimeError):
            remote_receivers.poll_once()
    assert [(row["receiver"], row["channel"], row["via"]) for row in rows] == [("SP9XDG", "wx", "RF"), ("SP9XDG", "lora", "SP9XDG-2")]


def test_the_analyzer_lists_what_the_remote_receiver_heard() -> None:
    from app.desktop import remote_receivers
    from app.desktop.station_analysis import analyze_station

    now = time.time()
    with station_data():
        frame(10, "SP9ABC>APRS,WIDE1-1:!5005.00N/01940.00E-QTH")
        remote_receivers.store_rows("SP9XDG", remote_receivers.rows_of_stats(stats(
            ("wx", now - 600, "SP9ABC", "RF", "WIDE1-1"),
            ("wx", now - 300, "SP9ABC", "SR9XSR", "WIDE1*"),
            ("lora", now - 7200, "SP9ABC", "RF", ""),  # outside the 1 h period
        )))
        result = analyze_station("SP9ABC", hours=1)
    remote = next(entry for entry in result["hearers"] if entry.get("remote"))
    assert (remote["call"], remote["n"], remote["direct"], remote["via"], remote["src"]) == \
        ("SP9XDG (144.800)", 2, 1, [("SR9XSR", 1)], ["remote"])
    assert result["remote_receivers"] == ["SP9XDG"]
