"""The digipeater repeats frames with UTF-8 text (Polish letters, emoji) byte for byte (the user: SQ2RBY-13's
messages with an emoji were refused with "AX.25 APRS information must use 7-bit ASCII")."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from app.db import execute, init_db
from app.services.outbound import build_tnc2_kiss_frame
from app.services.rf_tx_dispatcher import RfTxDispatcher

EMOJI_LINE = "SQ2RBY-13>APN000,SP9TKN*,WIDE2-1::SP2ST-3  :SQ2RBY ROBI SWOJE \U0001F609 zażółć{12"


def test_digipeat_keeps_utf8_text_byte_for_byte() -> None:
    frame = build_tnc2_kiss_frame(EMOJI_LINE, allow_utf8=True)
    assert "SWOJE \U0001F609 zażółć{12".encode("utf-8") in frame
    with pytest.raises(ValueError, match="7-bit ASCII"):
        build_tnc2_kiss_frame(EMOJI_LINE)  # own frames (beacons, messages) stay 7-bit as before
    with pytest.raises(ValueError, match="7-bit ASCII"):  # bytes that were not UTF-8 cannot be restored
        build_tnc2_kiss_frame("SP9XG-6>APOG77,SP9TKN*,WIDE2-1:!field day��", allow_utf8=True)
    assert build_tnc2_kiss_frame("SP8ABC>APRS,WIDE1-1:>test", allow_utf8=True) == build_tnc2_kiss_frame("SP8ABC>APRS,WIDE1-1:>test")


def test_digi_dispatcher_sends_the_emoji_frame() -> None:
    sent: list[bytes] = []

    class Monitor:
        async def send_outbound_frame(self, *, interface_id: int | None, frame: bytes) -> bool:
            sent.append(frame)
            return True

    async def run() -> None:
        dispatcher = RfTxDispatcher(traffic_monitor=Monitor(), min_tx_gap_seconds=0)
        await dispatcher.start()
        try:
            accepted, _detail = dispatcher.enqueue_digi_tx(interface_name="TNC1", line=EMOJI_LINE, frame_uid="utf8-1")
            assert accepted
            await dispatcher.wait_until_idle()
        finally:
            await dispatcher.stop()

    with tempfile.TemporaryDirectory() as temp_dir:
        previous = os.environ.get("APRSBOX_DB_PATH")
        os.environ["APRSBOX_DB_PATH"] = str(Path(temp_dir) / "aprsbox-test.db")
        try:
            init_db()
            execute("""INSERT INTO modems(name, modem_type, band, device_path, enabled, notes, created_at, updated_at)
                       VALUES ('TNC1', 'TCP', '2m', '127.0.0.1:9001', 1, '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')""")
            asyncio.run(run())
        finally:
            if previous is None:
                os.environ.pop("APRSBOX_DB_PATH", None)
            else:
                os.environ["APRSBOX_DB_PATH"] = previous
    assert len(sent) == 1 and "\U0001F609".encode("utf-8") in sent[0]
