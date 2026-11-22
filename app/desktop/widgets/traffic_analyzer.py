"""Traffic analyzer card of the station detail page (rules: SP9DLM, see app.desktop.station_analysis)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from app.desktop.i18n import t, tf
from app.desktop.tasks import run_async
from app.desktop.widgets.common import Badge, Card, checkbox, clear_layout, key_value_grid, label
from app.desktop.widgets.table import Column, DataTable

DEFAULT_HOURS = 24
LEVELS = {"ok": ("OK", "ok"), "warn": ("Warning", "warn"), "bad": ("Problem", "error"), "info": ("Info", "neutral")}
KIND_NAMES = {"beacon": "beacon", "msg": "message", "telem": "telemetry", "status": "status", "object": "object",
              "ack": "ACK", "bulletin": "bulletin", "thirdparty": "third-party", "other": "other"}
HEARD_SOURCES = {"rx": "our receiver", "aprsis": "APRS-IS", "findu": "findu.com", "remote": "its receive page"}


def when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%d.%m %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return "-"


def kinds_text(kinds: dict[str, int]) -> str:
    return " · ".join(f"{t(KIND_NAMES.get(kind, kind))} {count}" for kind, count in sorted(kinds.items(), key=lambda item: -item[1]))


def calls_text(pairs: list[Any], limit: int = 6) -> str:
    return ", ".join(f"{call} ({count})" for call, count in list(pairs)[:limit]) or "-"


def minutes_text(value: Any) -> str:
    return tf("{minutes} min", minutes=value) if value is not None else "-"


def table(columns: list[Column], rows: list[dict[str, Any]], visible_rows: int = 8) -> DataTable:
    widget = DataTable(columns, row_height=30, sortable=False)
    widget.set_rows(rows)
    widget.setMinimumHeight(36 + 30 * max(1, min(len(rows), visible_rows)))
    widget.setMaximumHeight(36 + 30 * max(1, min(len(rows), visible_rows)) + 4)
    return widget


class TrafficAnalyzerCard(Card):
    """Hidden until the station page's "Traffic analyzer" button; a period and findu.com can be chosen."""

    def __init__(self, parent: QWidget | None = None, *, on_shown: Callable[[], None] | None = None) -> None:
        super().__init__(t("Traffic analyzer"), t("Analysis rules: SP9DLM (APRS ANALIZER)."), parent=parent)
        self.callsign = ""
        self.hours = DEFAULT_HOURS
        self._running = False
        self._on_shown = on_shown
        controls = QHBoxLayout()
        controls.addWidget(label(t("Period"), "muted"))
        self.period_group = QButtonGroup(self)
        self.period_group.setExclusive(True)
        self.period_buttons: dict[int, QToolButton] = {}
        from app.desktop.station_analysis import PERIOD_HOURS

        for hours in PERIOD_HOURS:
            period = QToolButton()
            period.setProperty("variant", "secondary")
            period.setCheckable(True)
            period.setText(f"{hours} h")
            period.setMinimumSize(QSize(52, 28))
            period.clicked.connect(lambda _checked=False, value=hours: self._choose(value))
            self.period_group.addButton(period)
            self.period_buttons[hours] = period
            controls.addWidget(period)
        self.period_buttons[self.hours].setChecked(True)
        controls.addSpacing(12)
        self.findu = checkbox(t("Also findu.com (the whole APRS-IS, slower)"), True)
        self.findu.toggled.connect(lambda _checked: self.run())
        controls.addWidget(self.findu)
        controls.addStretch(1)
        self.add(controls)
        self.status = label("", "muted", wrap=True)
        self.add(self.status)
        self.results = QVBoxLayout()
        self.results.setSpacing(10)
        host = QWidget()
        host.setLayout(self.results)
        self.add(host)
        self.setVisible(False)

    # ------------------------------------------------------------------ actions
    def start(self, callsign: str) -> None:
        """The button: show the card and analyse (again)."""
        self.callsign = str(callsign or "").strip().upper()
        self.setVisible(True)
        self.run()

    def reset(self) -> None:
        """Another station was opened: the old result goes."""
        self.callsign = ""
        clear_layout(self.results)
        self.status.setText("")
        self.setVisible(False)

    def _choose(self, hours: int) -> None:
        self.hours = hours
        self.run()

    def run(self) -> None:
        if not self.callsign or not self.isVisible() or self._running:
            return
        from app.desktop.station_analysis import analyze_station

        self._running = True
        for period in self.period_buttons.values():
            period.setEnabled(False)
        self.findu.setEnabled(False)
        self.status.setText(tf("Analysing {callsign} ({hours} h)…", callsign=self.callsign, hours=self.hours))
        callsign, hours, use_findu = self.callsign, self.hours, self.findu.isChecked()
        run_async(lambda: analyze_station(callsign, hours=hours, use_findu=use_findu),
                  lambda result: self._done(callsign, result), self._failed, owner=self)

    def _finish(self) -> None:
        self._running = False
        for period in self.period_buttons.values():
            period.setEnabled(True)
        self.findu.setEnabled(True)

    def _failed(self, error: BaseException) -> None:
        self._finish()
        self.status.setText(tf("The analysis failed: {error}", error=str(error) or error.__class__.__name__))

    def _done(self, callsign: str, result: dict[str, Any]) -> None:
        self._finish()
        if callsign != self.callsign:
            return  # another station was opened meanwhile
        if result.get("error"):
            self.status.setText(t("Invalid callsign."))
            return
        self.render(result)
        if self._on_shown is not None:
            self._on_shown()

    # ---------------------------------------------------------------- rendering
    def _section(self, title: str, widget: QWidget | None = None, note: str = "") -> None:
        self.results.addWidget(label(title, "h3"))
        if note:
            self.results.addWidget(label(note, "muted", wrap=True))
        if widget is not None:
            self.results.addWidget(widget)

    def render(self, result: dict[str, Any]) -> None:
        clear_layout(self.results)
        self.status.setText(tf("{callsign}: the last {hours} h, analysed {time}. RF from the traffic logs, APRS-IS since {aprsis}.",
                               callsign=result.get("call"), hours=result.get("hours"), time=when(result.get("now")),
                               aprsis=when(result.get("aprsis_from"))))
        # Verdicts first: the point of the analysis.
        verdicts = QVBoxLayout()
        for verdict in result.get("verdicts") or []:
            caption, tone = LEVELS.get(str(verdict.get("level")), ("Info", "neutral"))
            row = QHBoxLayout()
            badge = Badge(t(caption), tone)
            badge.setMinimumWidth(80)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            params = dict(verdict.get("params") or {})
            if "role" in params:
                params["role"] = t(str(params["role"]))
            row.addWidget(label(tf(str(verdict.get("text") or ""), **params), None, wrap=True, selectable=True), 1)
            verdicts.addLayout(row)
        host = QWidget()
        host.setLayout(verdicts)
        self._section(t("Conclusions"), host)
        facts = []
        if result.get("dist_km") is not None:
            facts.append((t("Distance from my station"), f"{result['dist_km']} km"))
        if "mobile" in result:
            facts.append((t("Station"), t("mobile") if result.get("mobile") else t("fixed")))
        if facts:
            self.results.addWidget(key_value_grid(facts))

        bands = result.get("bands") or {}
        band_rows = [{"name": name, **row} for name, row in bands.items()]
        self._section(t("Traffic generated by the callsign"), table([
            Column("name", t("Interface"), width=130),
            Column("own_unique", t("Transmissions"), width=110),
            Column("own_heard", t("Copies heard"), width=110),
            Column("copies_avg", t("Copies per frame"), width=120, display=lambda row: row.get("copies_avg") or "-"),
            Column("beacon_iv_min", t("Beacon every"), width=110, display=lambda row: minutes_text(row.get("beacon_iv_min"))),
            Column("telem_iv_min", t("Telemetry every"), width=120, display=lambda row: minutes_text(row.get("telem_iv_min"))),
            Column("kinds", t("Kinds"), width=260, display=lambda row: kinds_text(row.get("kinds") or {}) or "-"),
            Column("via", t("Repeated by"), stretch=True, display=lambda row: calls_text(row.get("via") or [])),
        ], band_rows), t("A transmission and its copies within 60 s count once; the copies are the echoes from digipeaters."))

        paths = result.get("paths") or []
        self._section(t("WIDE path (sent; from frames heard directly)"), table([
            Column("path", t("Path"), width=220, display=lambda row: row.get("path") or t("(no path)")),
            Column("n", t("Frames"), width=90),
            Column("hops", t("Hops"), width=80),
            Column("last", t("Last"), stretch=True, display=lambda row: when(row.get("last"))),
        ], paths) if paths else None, "" if paths else t("No frame of it heard directly in the period."))

        self._section(t("Digipeater work (whose frames it repeats)"), table([
            Column("name", t("Interface"), width=130),
            Column("digi_n", t("Repeated"), width=100),
            Column("digi_per_h", t("Per hour"), width=90),
            Column("digi_first", t("First hop"), width=100),
            Column("digi_next", t("Further hop"), width=100),
            Column("digi_dups", t("Twice"), width=80),
            Column("digi_sources", t("Whose"), stretch=True, display=lambda row: calls_text(row.get("digi_sources") or [])),
        ], band_rows))

        hearers = result.get("hearers") or []
        self._section(t("Who hears it on RF"), table([
            Column("call", t("Station / receiver"), width=160),
            Column("n", t("Frames"), width=80),
            Column("direct", t("Directly"), width=90),
            Column("via", t("Via"), width=220, display=lambda row: calls_text(row.get("via") or [])),
            Column("dist_km", t("Distance"), width=100, display=lambda row: f"{row['dist_km']} km" if row.get("dist_km") is not None else "-"),
            Column("src", t("Source"), stretch=True, display=lambda row: ", ".join(t(HEARD_SOURCES.get(item, item)) for item in row.get("src") or [])),
        ], hearers, visible_rows=10) if hearers else None, "" if hearers else t("Nobody heard it on RF in the period."))
        for name in result.get("remote_receivers") or []:
            self.results.addWidget(label(tf("{name}: from its receive page (the last 40 rows, read every minute; gaps are "
                                            "possible).", name=name), "small", wrap=True))

        aprsis = result.get("aprsis") or {}
        fu = result.get("findu") or {}
        rows = [
            (t("Its frames in the APRS-IS stream"), aprsis.get("n", 0)),
            (t("Directly over the internet (TCPIP)"), aprsis.get("tcpip", 0)),
            (t("Gateways that put it in APRS-IS"), calls_text(aprsis.get("igates") or [], 10)),
            (t("As an iGate it sent"), tf("{frames} frames of {stations} stations: {calls}", frames=aprsis.get("igate_n", 0),
                                         stations=aprsis.get("igate_stations", 0), calls=calls_text(aprsis.get("igate_sources") or []))),
        ]
        if fu.get("off"):
            rows.append(("findu.com", t("off")))
        elif fu.get("ok"):
            rows.append(("findu.com", tf("{count} positions, gateways: {calls}", count=fu.get("n", 0), calls=calls_text(fu.get("igates") or [], 10))))
        else:
            rows.append(("findu.com", tf("not available ({error})", error=fu.get("err") or "-")))
        self._section(t("APRS-IS"), key_value_grid(rows))

        is2rf = [{"name": name, **example} for name, row in bands.items() for example in row.get("is2rf_ex") or []]
        counts = " · ".join(tf("{name}: messages {messages}, other {other}", name=name, messages=row.get("is2rf_msg", 0),
                               other=row.get("is2rf_other", 0)) for name, row in bands.items())
        self._section(t("Internet frames it put on RF (IS→RF)"), table([
            Column("t", t("Time"), width=130, display=lambda row: when(row.get("t"))),
            Column("name", t("Interface"), width=120),
            Column("src", t("Station from APRS-IS"), width=150),
            Column("text", t("Content"), stretch=True, mono=True),
        ], is2rf) if is2rf else None, counts)

        messages = [{"source": name, "from": result.get("call"), **message} for name, row in bands.items() for message in row.get("msgs") or []]
        messages += [{"source": "findu.com", **message} for message in fu.get("msgs") or []]
        messages.sort(key=lambda row: row.get("t") or 0, reverse=True)
        self._section(t("Messages"), table([
            Column("t", t("Time"), width=130, display=lambda row: when(row.get("t"))),
            Column("from", t("From"), width=110),
            Column("to", t("To"), width=110),
            Column("text", t("Content"), stretch=True),
            Column("source", t("Source"), width=120),
        ], messages[:20]) if messages else None, "" if messages else t("No messages of it in the period."))

        recent = [{"name": name, **frame} for name, row in bands.items() for frame in row.get("last") or []]
        recent.sort(key=lambda row: row.get("t") or 0, reverse=True)
        self._section(t("Recent frames of this callsign"), table([
            Column("t", t("Time"), width=130, display=lambda row: when(row.get("t"))),
            Column("name", t("Interface"), width=120),
            Column("k", t("Kind"), width=100, display=lambda row: t(KIND_NAMES.get(str(row.get("k")), str(row.get("k"))))),
            Column("path", t("Path (1st copy)"), width=200),
            Column("copies", t("Copies"), width=80),
            Column("via", t("Repeated by"), width=180, display=lambda row: ", ".join(row.get("via") or []) or "-"),
            Column("text", t("Content"), stretch=True, mono=True),
        ], recent[:15], visible_rows=12) if recent else None, "" if recent else t("No frame of it heard in the period."))
