"""Every APRS message frame heard or sent on RF (2 m, LoRa 70 cm) - the read-only "RF traffic" channel of Messages.

The user's choice (2026-11-22): everything in message format (telemetry PARM/UNIT/EQNS/BITS and ACK/REJ too),
messages to the own station and groups too, every copy separately (with band and path), the own and the
digipeated transmissions too, kept for good, no unread mark or notification.  It is a log next to the
conversations: nothing here sends, ACKs or changes a conversation.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.db import fetch_all, fetch_one, get_app_setting, get_connection, log_event, set_app_setting

ORIGIN_RX = "rx"  # heard on RF
ORIGIN_OWN = "own"  # sent by this station
ORIGIN_DIGI = "digi"  # another station's message repeated by this station's digipeater
ORIGIN_APRSIS_TO_RF = "aprsis_to_rf"  # gated from APRS-IS to RF by this station
ORIGIN_PROXY = "proxy"  # sent by a client of an exposed KISS port

KIND_MESSAGE = "message"
KIND_ACK = "ack"
KIND_REJ = "rej"
KIND_TELEMETRY = "telemetry"
KIND_BULLETIN = "bulletin"
KIND_QUERY = "query"

_ACK_RE = re.compile(r"(ack|rej)[0-9A-Z]{1,5}(}[0-9A-Z]{0,5})?", re.IGNORECASE)
_TELEMETRY_PREFIXES = ("PARM.", "UNIT.", "EQNS.", "BITS.")
_NUMBER_RE = re.compile(r"^(?P<text>.*?)\{(?P<number>[0-9A-Za-z]{1,5})(?:}(?P<reply>[0-9A-Za-z]{0,5}))?$", re.DOTALL)
BACKFILL_SETTING_KEY = "rf_message_log.backfilled_at"


def parse_rf_message(line: str) -> dict[str, str] | None:
    """Sender, addressee, text and kind of a message-format TNC2 line, or None for any other frame.

    A third-party frame (an iGate puts an internet message on RF) gives the inner sender and addressee; the
    RF path stays the one the frame was heard with, and ``relay`` names the station that put it on RF.
    """
    from app.services.messages import _parse_tnc2_line

    outer = _parse_tnc2_line(str(line or ""))
    if outer is None:
        return None
    inner = outer
    relay = ""
    info = str(outer.get("info") or "")
    if info.startswith("}"):
        inner = _parse_tnc2_line(info[1:].lstrip())
        if inner is None:
            return None
        relay = str(outer.get("source") or "").strip().upper()
        info = str(inner.get("info") or "")
    if not info.startswith(":") or len(info) < 11 or info[10] != ":":
        return None
    addressee = info[1:10].strip().upper()
    sender = str(inner.get("source") or "").strip().upper()
    if not addressee or not sender:
        return None
    text = info[11:]
    number = ""
    if _ACK_RE.fullmatch(text):
        kind = KIND_ACK if text[:3].lower() == "ack" else KIND_REJ
    elif text.startswith(_TELEMETRY_PREFIXES):
        kind = KIND_TELEMETRY
    elif addressee.startswith("BLN"):
        kind = KIND_BULLETIN
    elif text.startswith("?"):
        kind = KIND_QUERY
    else:
        kind = KIND_MESSAGE
    if kind in {KIND_MESSAGE, KIND_BULLETIN, KIND_QUERY}:
        match = _NUMBER_RE.match(text)
        if match:
            text, number = match.group("text"), match.group("number")
    return {
        "sender": sender,
        "addressee": addressee,
        "text": text,
        "number": number,
        "kind": kind,
        "path": str(outer.get("path") or "").strip(),
        "relay": relay,
    }


def record_rf_message_frame(
    connection: sqlite3.Connection,
    *,
    line: str,
    direction: str,
    origin: str,
    interface_id: int | None,
    interface_name: str,
    band: str,
    created_at: str,
) -> bool:
    """Store one copy if ``line`` is a message frame (inside the caller's transaction). Never raises."""
    try:
        parsed = parse_rf_message(line)
        if parsed is None:
            return False
        connection.execute(
            """
            INSERT INTO rf_message_log(
                created_at, direction, origin, interface_id, interface_name, band,
                sender, addressee, message_text, message_number, kind, path, relay, line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                direction,
                origin,
                interface_id,
                str(interface_name or "").strip(),
                str(band or "").strip(),
                parsed["sender"],
                parsed["addressee"],
                parsed["text"],
                parsed["number"],
                parsed["kind"],
                parsed["path"],
                parsed["relay"],
                str(line or ""),
            ),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the RF traffic log must never cost a frame
        try:
            log_event("WARNING", "messages", f"RF traffic log entry failed: {exc!r}")
        except Exception:
            pass
        return False


def tx_origin(*, line: str, source_kind: str, command: str) -> str | None:
    """Origin of a transmitted frame for the log, or None when it was not sent on RF."""
    normalized_kind = str(source_kind or "rf").strip().lower()
    normalized_command = str(command or "TX").strip().upper()
    if normalized_command == "TX-PROXY":
        return ORIGIN_PROXY
    if normalized_command != "TX" or normalized_kind not in {"rf", "aprsis_to_rf"}:
        return None  # TX-SKIP and APRS-IS uplinks are no RF transmission
    if normalized_kind == "aprsis_to_rf":
        return ORIGIN_APRSIS_TO_RF
    from app.services.messages import _callsign_identity_matches, _local_station_identity

    parsed = parse_rf_message(line)
    own = _local_station_identity()
    if parsed is not None and own and _callsign_identity_matches(parsed["sender"], own):
        return ORIGIN_OWN
    return ORIGIN_DIGI


def list_rf_message_log(*, limit: int) -> dict[str, Any]:
    """The newest ``limit`` copies, oldest first (as a conversation reads), and the total count."""
    normalized_limit = max(1, int(limit))
    rows = fetch_all(
        """
        SELECT id, created_at, direction, origin, interface_id, interface_name, band,
               sender, addressee, message_text, message_number, kind, path, relay
        FROM rf_message_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (normalized_limit,),
    )
    total_row = fetch_one("SELECT COUNT(*) AS total FROM rf_message_log")
    items = [
        {
            "id": f"rf-{int(row['id'])}",
            "timestamp": str(row["created_at"] or ""),
            "direction": str(row["direction"] or ""),
            "origin": str(row["origin"] or ""),
            "interface_name": str(row["interface_name"] or ""),
            "band": str(row["band"] or ""),
            "sender": str(row["sender"] or ""),
            "addressee": str(row["addressee"] or ""),
            "text": str(row["message_text"] or ""),
            "message_number": str(row["message_number"] or ""),
            "kind": str(row["kind"] or ""),
            "path": str(row["path"] or ""),
            "relay": str(row["relay"] or ""),
        }
        for row in reversed(rows)
    ]
    return {"items": items, "total": int(total_row["total"]) if total_row else len(items)}


def clear_rf_message_log() -> int:
    """Empty the channel (the user clears it on the Messages page); new frames keep coming in."""
    with get_connection() as connection:
        return int(connection.execute("DELETE FROM rf_message_log").rowcount)


def backfill_rf_message_log() -> int:
    """Once: the message frames still kept in the traffic log (the retention, 6 h at the station) go in."""
    if str(get_app_setting(BACKFILL_SETTING_KEY) or "").strip():
        return 0
    rows = fetch_all(
        """
        SELECT created_at, direction, source_kind, interface_id, source, band, line, command
        FROM traffic_frames
        WHERE source_kind IN ('rf', 'aprsis_to_rf')
          AND direction IN ('rx', 'tx')
          AND line LIKE '%::%'
        ORDER BY id ASC
        """
    )
    stored = 0
    with get_connection() as connection:
        for row in rows:
            direction = str(row["direction"] or "")
            if direction == "rx":
                origin: str | None = ORIGIN_RX if str(row["source_kind"]) == "rf" else None
            else:
                origin = tx_origin(line=str(row["line"]), source_kind=str(row["source_kind"]),
                                   command=str(row["command"] or "TX"))
            if origin is None:
                continue
            if record_rf_message_frame(
                connection,
                line=str(row["line"]),
                direction=direction,
                origin=origin,
                interface_id=row["interface_id"],
                interface_name=str(row["source"] or ""),
                band=str(row["band"] or ""),
                created_at=str(row["created_at"]),
            ):
                stored += 1
    from app.db import utc_now

    set_app_setting(BACKFILL_SETTING_KEY, utc_now())
    return stored
