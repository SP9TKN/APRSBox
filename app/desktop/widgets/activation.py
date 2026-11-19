"""Activation schedule fields shared by objects, items and bulletins (web: section.html)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from PySide6.QtCore import QDate, QDateTime, QTime, QTimeZone, Signal
from PySide6.QtWidgets import QDateTimeEdit, QHBoxLayout, QSpinBox, QVBoxLayout, QWidget

from app.desktop.i18n import t, tf
from app.desktop.widgets.common import FormGrid, button, combo, combo_value, label, set_combo_value

ACTIVATION_MODES = [("manual", "Manual"), ("scheduled", "Scheduled"), ("recurring", "Recurring")]
RECURRENCE_UNITS = [("", "Select"), ("day", "Day(s)"), ("week", "Week(s)"), ("month", "Month(s)"), ("year", "Year(s)")]
UNIT_LABELS = {"day": "Day(s)", "week": "Week(s)", "month": "Month(s)", "year": "Year(s)"}
DATETIME_FORMAT = "yyyy-MM-dd HH:mm"


def _utc(year: int, month: int, day: int, hour: int, minute: int) -> QDateTime:
    return QDateTime(QDate(year, month, day), QTime(hour, minute), QTimeZone.utc())


_EMPTY = _utc(2000, 1, 1, 0, 0)


def _now_utc() -> QDateTime:
    now = datetime.now(timezone.utc)
    return _utc(now.year, now.month, now.day, now.hour, now.minute)


def parse_form_datetime(value: Any) -> QDateTime | None:
    """'YYYY-MM-DD', 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM' (dates alone mean 23:59, as in the web form)."""
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    if len(text) == 10:
        text += " 23:59"
    try:
        parsed = datetime.strptime(text[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return _utc(parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute)


class UtcDateTimeEdit(QDateTimeEdit):
    """UTC date/time field that can be empty (shows nothing until the user picks a value)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTimeZone(QTimeZone.utc())
        self.setDisplayFormat(DATETIME_FORMAT)
        self.setCalendarPopup(True)
        self.setMinimumDateTime(_EMPTY)
        self.setSpecialValueText(" ")
        self.clear_value()

    def is_empty(self) -> bool:
        return self.dateTime() <= _EMPTY

    def clear_value(self) -> None:
        self.setDateTime(_EMPTY)

    def set_value(self, value: Any) -> None:
        parsed = parse_form_datetime(value)
        if parsed is None:
            self.clear_value()
        else:
            self.setDateTime(parsed)

    def value(self) -> str:
        if self.is_empty():
            return ""
        return self.dateTime().toUTC().toString(DATETIME_FORMAT)

    def _start_editing(self) -> None:
        if self.is_empty():
            self.setDateTime(_now_utc())

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self._start_editing()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if event.text() and event.text().isprintable():
            self._start_editing()
        super().keyPressEvent(event)


def _optional_spin(maximum: int) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(0, maximum)
    widget.setSpecialValueText(" ")  # 0 = not set
    return widget


class ActivationScheduleEditor(QWidget):
    """Activation, active from/until, recurrence, summary and warnings."""

    changed = Signal()

    def __init__(self, path_value: Callable[[], str], interval_value: Callable[[], Any], parent: QWidget | None = None,
                 *, columns: int = 3) -> None:
        super().__init__(parent)
        self._path_value = path_value
        self._interval_value = interval_value
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        grid = FormGrid(columns)
        self.mode = combo(ACTIVATION_MODES, "manual")
        grid.add_field("activation_mode", t("Activation"), self.mode)
        if columns == 2:
            grid.add_widget(QWidget(), span=1)
        self.active_from = UtcDateTimeEdit()
        self.active_until = UtcDateTimeEdit()
        grid.add_field("active_from_utc", t("Active from"), self._with_clear(self.active_from))
        grid.add_field("active_until_utc", t("Active until"), self._with_clear(self.active_until))
        self.duration_hours = _optional_spin(100_000)
        self.duration_hours.setSuffix(" h")
        grid.add_field("recurrence_duration_minutes", t("Active for (hours)"), self.duration_hours)
        self.interval = _optional_spin(10_000)
        grid.add_field("recurrence_interval_value", t("Repeat every"), self.interval)
        self.unit = combo(RECURRENCE_UNITS, "")
        grid.add_field("recurrence_interval_unit", t("Repeat unit"), self.unit)
        self.grid = grid
        outer.addWidget(grid)
        panel = QVBoxLayout()
        panel.setSpacing(3)
        panel.addWidget(label(t("Activation summary"), "h3"))
        self.summary = label(t("Manual activation."), "muted", wrap=True)
        panel.addWidget(self.summary)
        self.warnings = label("", "warning", wrap=True)
        self.warnings.hide()
        panel.addWidget(self.warnings)
        outer.addLayout(panel)
        self.mode.currentIndexChanged.connect(self.sync)
        for widget in (self.active_from, self.active_until):
            widget.dateTimeChanged.connect(lambda _value: self.sync())
        for widget in (self.duration_hours, self.interval):
            widget.valueChanged.connect(lambda _value: self.sync())
        self.unit.currentIndexChanged.connect(self.sync)
        self.sync()

    def _with_clear(self, edit: UtcDateTimeEdit) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(edit, 1)
        clear = button(t("Clear date"), variant="secondary", on_click=lambda: (edit.clear_value(), self.sync()))
        layout.addWidget(clear)
        edit.clear_button = clear  # type: ignore[attr-defined]
        return container

    # ------------------------------------------------------------------ data
    def load(self, row: dict[str, Any] | None) -> None:
        row = row or {}
        widgets = (self.mode, self.active_from, self.active_until, self.duration_hours, self.interval, self.unit)
        for widget in widgets:
            widget.blockSignals(True)
        set_combo_value(self.mode, str(row.get("activation_mode") or "manual"))
        self.active_from.set_value(row.get("activation_form_active_from_utc"))
        self.active_until.set_value(row.get("activation_form_active_until_utc"))
        try:
            minutes = int(row.get("recurrence_duration_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        self.duration_hours.setValue(minutes // 60 if minutes > 0 else 0)
        try:
            self.interval.setValue(int(row.get("recurrence_interval_value") or 0))
        except (TypeError, ValueError):
            self.interval.setValue(0)
        set_combo_value(self.unit, str(row.get("recurrence_interval_unit") or ""))
        for widget in widgets:
            widget.blockSignals(False)
        self.sync()

    def values(self) -> dict[str, str]:
        """Form fields as the web form posts them (disabled fields are sent empty)."""
        mode = str(combo_value(self.mode) or "manual")
        recurring = mode == "recurring"
        hours = self.duration_hours.value()
        return {
            "activation_mode": mode,
            "active_from_utc": self.active_from.value() if mode in {"scheduled", "recurring"} else "",
            "active_until_utc": self.active_until.value(),
            "recurrence_duration_minutes": str(hours * 60) if recurring and hours > 0 else "",
            "recurrence_interval_value": str(self.interval.value()) if recurring and self.interval.value() > 0 else "",
            "recurrence_interval_unit": str(combo_value(self.unit) or "") if recurring else "",
        }

    # ------------------------------------------------------------- behaviour
    def sync(self) -> None:
        mode = str(combo_value(self.mode) or "manual")
        from_enabled = mode in {"scheduled", "recurring"}
        recurring = mode == "recurring"
        self.active_from.setEnabled(from_enabled)
        self.active_from.clear_button.setEnabled(from_enabled)  # type: ignore[attr-defined]
        for widget in (self.duration_hours, self.interval, self.unit):
            widget.setEnabled(recurring)
        for name in ("active_from_utc",):
            self.grid.rows[name].setEnabled(from_enabled)
        for name in ("recurrence_duration_minutes", "recurrence_interval_value", "recurrence_interval_unit"):
            self.grid.rows[name].setEnabled(recurring)
        self.summary.setText(self._summary(mode))
        warnings = self._warnings(mode)
        self.warnings.setText("\n".join(warnings))
        self.warnings.setVisible(bool(warnings))
        self.changed.emit()

    def _summary(self, mode: str) -> str:
        active_from = self.active_from.value()
        active_until = self.active_until.value()
        if mode == "scheduled":
            return tf("Active from {fromDate} UTC to {toDate} UTC.", fromDate=active_from or "?", toDate=active_until or "?")
        if mode == "recurring":
            hours = self.duration_hours.value()
            unit = str(combo_value(self.unit) or "")
            text = tf(
                "Active every {value} {unit} from {fromDate} UTC for {duration}.",
                value=self.interval.value() or "?",
                unit=t(UNIT_LABELS[unit]) if unit in UNIT_LABELS else "?",
                fromDate=active_from or "?",
                duration=f"{hours}h" if hours > 0 else "?",
            )
            if active_until:
                text += " " + tf("Repeat until {repeatUntil} UTC.", repeatUntil=active_until)
            return text
        if active_until:
            return tf("Manual activation. Valid until: {validUntil} UTC.", validUntil=active_until)
        return t("Manual activation.")

    def _warnings(self, mode: str) -> list[str]:
        warnings: list[str] = []
        if mode == "recurring" and not self.active_until.value():
            warnings.append(t("Recurring schedule has no end date."))
        if mode == "recurring" and self.duration_hours.value() * 60 > 1440:
            warnings.append(t("Record will be active for more than 24h per cycle."))
        path = str(self._path_value() or "").strip().upper()
        try:
            interval = int(self._interval_value())
        except (TypeError, ValueError):
            interval = None
        if "WIDE2-2" in path and interval is not None and interval < 60:
            warnings.append(t("WIDE2-2 with interval below 60m is not recommended."))
        if path:
            warnings.append(t("Direct path is recommended for local/simple records."))
        return warnings
