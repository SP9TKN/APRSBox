"""Weather charts of a station (the user's choice, after pogodacwop.org and aprs.fi).

The last 6 h to 7 days of temperature, humidity, pressure, wind, wind direction, rain and sunlight
from app.services.weather_history, drawn with QPainter (QtCharts is not part of PySide6-Essentials,
which the portable ZIP ships). The range buttons sit in one row above the charts; one crosshair moves
on every chart at once and each chart's header shows the value at that moment, or the latest one.
Colours are slots of the data-viz reference palette, validated for the light and dark card surfaces
(the pairs sharing a chart: blue/orange for wind, blue/aqua for rain). Text never takes a series
colour; a short line key beside it carries the identity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.desktop.i18n import t
from app.desktop.theme import current_colors
from app.desktop.widgets.common import label

# (light, dark) steps of the reference categorical palette.
PALETTE = {
    "blue": ("#2a78d6", "#3987e5"),
    "orange": ("#eb6834", "#d95926"),
    "aqua": ("#1baf7a", "#199e70"),
    "yellow": ("#eda100", "#c98500"),
    "violet": ("#4a3aa7", "#9085e9"),
    "red": ("#e34948", "#e66767"),
}
RANGES_HOURS = (6, 24, 72, 168)
DEFAULT_RANGE_HOURS = 24
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Series:
    key: str
    name: str
    color: str
    kind: str = "line"  # line | bars | dots


@dataclass(frozen=True)
class ChartSpec:
    title: str
    unit: str
    series: tuple[Series, ...]
    decimals: int = 1
    min_span: float = 1.0
    floor_zero: bool = False
    fixed: tuple[float, float] | None = None
    compass: bool = False


CHARTS = (
    ChartSpec("Temperature", "°C", (Series("temperature_c", "Temperature", "red"),), min_span=2),
    ChartSpec("Humidity", "%", (Series("humidity_percent", "Humidity", "aqua"),), decimals=0, fixed=(0, 100)),
    ChartSpec("Pressure", "hPa", (Series("pressure_hpa", "Pressure", "violet"),), min_span=4),
    ChartSpec("Wind", "m/s", (Series("wind_speed_ms", "average", "blue"), Series("wind_gust_ms", "gusts", "orange")),
              min_span=2, floor_zero=True),
    ChartSpec("Wind direction", "°", (Series("wind_dir_deg", "Wind direction", "blue", "dots"),), decimals=0,
              fixed=(0, 360), compass=True),
    ChartSpec("Rain", "mm", (Series("rain_1h_mm", "1 h", "blue", "bars"), Series("rain_24h_mm", "24 h", "aqua")),
              min_span=1, floor_zero=True),
    ChartSpec("Sunlight", "W/m²", (Series("luminosity_wm2", "Sunlight", "yellow"),), decimals=0, min_span=10,
              floor_zero=True),
)


def parse_observations(rows: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    """(epoch seconds, row) of the weather history rows, oldest first."""
    points = []
    for row in rows:
        try:
            moment = datetime.fromisoformat(str(row.get("observed_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        points.append((moment.timestamp(), row))
    points.sort(key=lambda item: item[0])
    return points


def nice_ticks(low: float, high: float, count: int = 4) -> list[float]:
    span = max(high - low, 1e-9)
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(factor * magnitude for factor in (1, 2, 2.5, 5, 10) if factor * magnitude >= raw)
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + step * 1e-6:
        ticks.append(round(value, 10))
        value += step
    return ticks


def format_number(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}" if decimals else f"{value:.0f}"


class WeatherChart(QWidget):
    hovered = Signal(object)  # epoch seconds under the pointer, or None

    HEADER = 26
    BOTTOM = 18

    def __init__(self, spec: ChartSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.points: list[tuple[float, dict[str, Any]]] = []
        self.start = 0.0
        self.end = 1.0
        self.hover_time: float | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(176)

    def set_data(self, points: list[tuple[float, dict[str, Any]]], start: float, end: float) -> None:
        self.points = [point for point in points if start <= point[0] <= end]
        self.start, self.end = start, end
        self.update()

    def set_hover(self, moment: float | None) -> None:
        if moment != self.hover_time:
            self.hover_time = moment
            self.update()

    def has_values(self) -> bool:
        return any(row.get(series.key) is not None and not self._calm(row)
                   for _time, row in self.points for series in self.spec.series)

    # ------------------------------------------------------------------ geometry
    def _plot_rect(self) -> QRectF:
        return QRectF(44, self.HEADER, max(10, self.width() - 52), max(10, self.height() - self.HEADER - self.BOTTOM))

    def _domain(self) -> tuple[float, float]:
        spec = self.spec
        if spec.fixed:
            return spec.fixed
        values = [float(row[s.key]) for _time, row in self.points for s in spec.series if row.get(s.key) is not None]
        if not values:
            return (0.0, spec.min_span)
        low, high = min(values), max(values)
        if spec.floor_zero:
            low = 0.0
        if high - low < spec.min_span:
            middle = (high + low) / 2
            low, high = (0.0, spec.min_span) if spec.floor_zero else (middle - spec.min_span / 2, middle + spec.min_span / 2)
        padding = (high - low) * 0.08
        return (low if spec.floor_zero else low - padding, high + padding)

    def _x(self, moment: float, plot: QRectF) -> float:
        return plot.left() + (moment - self.start) / max(1.0, self.end - self.start) * plot.width()

    def _y(self, value: float, plot: QRectF, domain: tuple[float, float]) -> float:
        low, high = domain
        return plot.bottom() - (value - low) / max(1e-9, high - low) * plot.height()

    def _nearest(self, moment: float) -> tuple[float, dict[str, Any]] | None:
        if not self.points:
            return None
        nearest = min(self.points, key=lambda point: abs(point[0] - moment))
        tolerance = max(600.0, (self.end - self.start) * 0.02)
        return nearest if abs(nearest[0] - moment) <= tolerance else None

    # --------------------------------------------------------------------- input
    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        plot = self._plot_rect()
        x = min(max(event.position().x(), plot.left()), plot.right())
        self.hovered.emit(self.start + (x - plot.left()) / plot.width() * (self.end - self.start))

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self.hovered.emit(None)
        super().leaveEvent(event)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        colors = current_colors()
        dark = colors.is_dark
        text = colors.color("text")
        muted = colors.color("muted")
        surface = QColor(colors.color("panel"))
        surface.setAlpha(255)
        grid = QColor(colors.color("border"))
        grid.setAlphaF(0.55)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot = self._plot_rect()
        domain = self._domain()
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))

        # Grid and y labels: hairlines, one step off the surface.
        painter.setFont(small)
        ticks = [0, 90, 180, 270, 360] if self.spec.compass else nice_ticks(*domain)
        for value in ticks:
            if not domain[0] <= value <= domain[1]:
                continue
            y = round(self._y(value, plot, domain)) + 0.5
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            caption = {0: "N", 90: "E", 180: "S", 270: "W", 360: "N"}[int(value)] if self.spec.compass else format_number(value, self.spec.decimals if abs(value) < 100 else 0)
            painter.setPen(muted)
            painter.drawText(QRectF(0, y - 8, plot.left() - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, caption)
        self._paint_time_axis(painter, plot, muted, grid)

        if not self.has_values():
            painter.setPen(muted)
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, t("No data in this range"))
            self._paint_header(painter, text, muted, None, dark)
            painter.end()
            return

        gap = self._gap_seconds()
        for series in self.spec.series:
            color = QColor(PALETTE[series.color][1 if dark else 0])
            if series.kind == "bars":
                self._paint_bars(painter, plot, domain, series, color, surface)
            elif series.kind == "dots":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                for moment, row in self.points:
                    value = row.get(series.key)
                    if value is not None and not self._calm(row):
                        painter.drawEllipse(QPointF(self._x(moment, plot), self._y(float(value), plot, domain)), 2.5, 2.5)
            else:
                self._paint_line(painter, plot, domain, series, color, surface, gap)

        hover = self._nearest(self.hover_time) if self.hover_time is not None else None
        if hover is not None:
            x = round(self._x(hover[0], plot)) + 0.5
            painter.setPen(QPen(muted, 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            for series in self.spec.series:
                value = hover[1].get(series.key)
                if value is not None and series.kind != "bars" and not self._calm(hover[1]):
                    self._dot(painter, QPointF(x, self._y(float(value), plot, domain)),
                              QColor(PALETTE[series.color][1 if dark else 0]), surface)
        self._paint_header(painter, text, muted, hover, dark)
        painter.end()

    def _calm(self, row: dict[str, Any]) -> bool:
        """No wind: a direction reported then (often 000) means nothing."""
        return self.spec.compass and not float(row.get("wind_speed_ms") or 0) and not float(row.get("wind_gust_ms") or 0)

    def _gap_seconds(self) -> float:
        times = [moment for moment, _row in self.points]
        if len(times) < 3:
            return 3600.0
        steps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
        median = steps[len(steps) // 2] if steps else 600.0
        # Stations report every 5 to 75 minutes (APRS-IS and CWOP drop some): only a long silence
        # breaks the line, a lost report or two must not cut it into pieces.
        return max(2 * 3600.0, median * 8)

    def _paint_line(self, painter: QPainter, plot: QRectF, domain: tuple[float, float], series: Series,
                    color: QColor, surface: QColor, gap: float) -> None:
        path = QPainterPath()
        previous: float | None = None
        last: QPointF | None = None
        segments: list[list[QPointF]] = []
        for moment, row in self.points:
            value = row.get(series.key)
            if value is None:
                continue
            point = QPointF(self._x(moment, plot), self._y(float(value), plot, domain))
            if previous is None or moment - previous > gap:
                path.moveTo(point)  # no line across a gap in the reports
                segments.append([point])
            else:
                path.lineTo(point)
                segments[-1].append(point)
            previous, last = moment, point
        pen = QPen(color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for segment in segments:
            if len(segment) == 1:  # a lone reading between gaps: a line of one point would not show
                painter.drawEllipse(segment[0], 2.5, 2.5)
        if last is not None:
            self._dot(painter, last, color, surface)

    def _paint_bars(self, painter: QPainter, plot: QRectF, domain: tuple[float, float], series: Series,
                    color: QColor, surface: QColor) -> None:
        # One bar per bucket of at least 8 px, the highest 1-hour rain in it: 24 px at most, 2 px apart.
        buckets = max(1, int(plot.width() // 8))
        size = (self.end - self.start) / buckets
        highest: dict[int, float] = {}
        for moment, row in self.points:
            value = row.get(series.key)
            if value is not None and float(value) > 0:
                index = min(buckets - 1, int((moment - self.start) // size))
                highest[index] = max(highest.get(index, 0.0), float(value))
        width = min(24.0, plot.width() / buckets - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        baseline = self._y(max(domain[0], 0.0), plot, domain)
        for index, value in highest.items():
            x = plot.left() + (index + 0.5) * plot.width() / buckets - width / 2
            top = self._y(value, plot, domain)
            radius = min(4.0, width / 2, max(0.0, baseline - top))
            bar = QPainterPath()
            bar.moveTo(x, baseline)
            bar.lineTo(x, top + radius)
            bar.quadTo(x, top, x + radius, top)
            bar.lineTo(x + width - radius, top)
            bar.quadTo(x + width, top, x + width, top + radius)
            bar.lineTo(x + width, baseline)
            bar.closeSubpath()
            painter.drawPath(bar)  # rounded data end, square at the baseline

    @staticmethod
    def _dot(painter: QPainter, center: QPointF, color: QColor, surface: QColor) -> None:
        painter.setPen(QPen(surface, 2))  # a 2 px ring in the surface colour
        painter.setBrush(color)
        painter.drawEllipse(center, 4.5, 4.5)

    def _paint_time_axis(self, painter: QPainter, plot: QRectF, muted: QColor, grid: QColor) -> None:
        hours = (self.end - self.start) / 3600
        step_hours = 1 if hours <= 6 else 3 if hours <= 24 else 12 if hours <= 72 else 24
        start_local = datetime.fromtimestamp(self.start)
        first = start_local.replace(minute=0, second=0, microsecond=0)
        while first.hour % step_hours or first.timestamp() < self.start:
            first += timedelta(hours=1)
        tick = first
        while tick.timestamp() <= self.end:
            x = round(self._x(tick.timestamp(), plot)) + 0.5
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 3))
            if step_hours >= 24 or (step_hours == 12 and tick.hour == 0):
                caption = f"{t(WEEKDAYS[tick.weekday()])} {tick:%d.%m}"
            else:
                caption = f"{tick:%H:%M}"
            painter.setPen(muted)
            width = painter.fontMetrics().horizontalAdvance(caption) + 2
            left = min(max(x - width / 2, 0.0), self.width() - width)  # measured, kept inside: never clipped
            painter.drawText(QRectF(left, plot.bottom() + 2, width, self.BOTTOM - 2), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, caption)
            tick += timedelta(hours=step_hours)

    def _paint_header(self, painter: QPainter, text: QColor, muted: QColor, hover: tuple[float, dict[str, Any]] | None,
                      dark: bool) -> None:
        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(text)
        painter.drawText(QRectF(4, 2, self.width() / 2, self.HEADER - 6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{t(self.spec.title)} ({self.spec.unit})")
        point = hover if hover is not None else self._latest()
        if point is None:
            return
        moment, row = point
        x = self.width() - 6
        painter.setFont(self.font())
        when = datetime.fromtimestamp(moment).strftime("%d.%m %H:%M")
        metrics = painter.fontMetrics()
        painter.setPen(muted)
        width = metrics.horizontalAdvance(when)
        painter.drawText(QRectF(x - width, 2, width, self.HEADER - 6), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, when)
        x -= width + 10
        for series in reversed(self.spec.series):
            value = row.get(series.key)
            if value is None:
                reading = "-"
            elif self.spec.compass:
                reading = t("calm") if self._calm(row) else self._compass_name(float(value))
            else:
                reading = format_number(float(value), self.spec.decimals)
            label_text = f"{reading}" + ("" if len(self.spec.series) == 1 else f" {t(series.name)}")
            bold = QFont(self.font())
            bold.setBold(True)
            painter.setFont(bold)
            width = painter.fontMetrics().horizontalAdvance(label_text)
            painter.setPen(text)  # values in the text colour, the line key carries the series
            painter.drawText(QRectF(x - width, 2, width, self.HEADER - 6), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label_text)
            x -= width + 4
            key = QPen(QColor(PALETTE[series.color][1 if dark else 0]), 2)
            key.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(key)
            middle = 2 + (self.HEADER - 6) / 2
            painter.drawLine(QPointF(x - 12, middle), QPointF(x - 2, middle))
            x -= 22

    def _latest(self) -> tuple[float, dict[str, Any]] | None:
        for moment, row in reversed(self.points):
            if any(row.get(series.key) is not None for series in self.spec.series):
                return moment, row
        return None

    @staticmethod
    def _compass_name(degrees: float) -> str:
        names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        return f"{names[int((degrees % 360) / 45 + 0.5) % 8]} {degrees:.0f}°"


class WeatherPanel(QWidget):
    """Range buttons in one row and the charts of the quantities the station reports."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.points: list[tuple[float, dict[str, Any]]] = []
        self.range_hours = DEFAULT_RANGE_HOURS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        self.range_group = QButtonGroup(self)
        self.range_group.setExclusive(True)
        self.range_buttons: dict[int, QPushButton] = {}
        for hours in RANGES_HOURS:
            button = QPushButton()  # the secondary variant has a checked look
            button.setText(t(f"{hours} h") if hours < 72 else t(f"{hours // 24} days"))
            button.setCheckable(True)
            button.setProperty("variant", "secondary")
            button.clicked.connect(lambda _checked=False, value=hours: self.set_range(value))
            self.range_group.addButton(button)
            self.range_buttons[hours] = button
            controls.addWidget(button)
        controls.addStretch(1)
        self.summary = label("", "muted")
        controls.addWidget(self.summary)
        layout.addLayout(controls)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        layout.addLayout(self.grid)
        self.charts = [WeatherChart(spec) for spec in CHARTS]
        for chart in self.charts:
            chart.hovered.connect(self._hover)
        self.range_buttons[self.range_hours].setChecked(True)

    def set_observations(self, rows: list[dict[str, Any]]) -> None:
        self.points = parse_observations(rows)
        self._render()

    def set_range(self, hours: int) -> None:
        self.range_hours = int(hours)
        self.range_buttons[self.range_hours].setChecked(True)
        self._render()

    def visible_charts(self) -> list[WeatherChart]:
        return [chart for chart in self.charts if chart.isVisibleTo(self)]

    def _render(self) -> None:
        end = datetime.now().timestamp()
        start = end - self.range_hours * 3600
        first = next((moment for moment, _row in self.points if moment >= start), None)
        if first is not None and first - start > 3600:
            # The history is shorter than the range (it is being collected): no empty left half.
            start = max(start, first - (end - first) * 0.03)
        self._style_range_buttons()
        while self.grid.count():
            self.grid.takeAt(0)
        shown = 0
        for chart in self.charts:
            # The quantities the station reports at all (7 days); an empty range still shows its chart.
            measured = any(row.get(series.key) is not None for _time, row in self.points for series in chart.spec.series)
            chart.setVisible(measured)
            if not measured:
                continue
            chart.set_data(self.points, start, end)
            self.grid.addWidget(chart, shown // 2, shown % 2)
            shown += 1
        count = sum(1 for moment, _row in self.points if moment >= start)
        since = f" · {t('since')} {datetime.fromtimestamp(first):%d.%m %H:%M}" if first is not None else ""
        self.summary.setText(f"{t('Weather reports')}: {count}{since}")

    def _style_range_buttons(self) -> None:
        # The palette's accent can be grey, so the checked range would look disabled: bold, in the
        # text colour and outlined instead.
        colors = current_colors()
        for button in self.range_buttons.values():
            button.setStyleSheet(
                f"QPushButton {{ color: {colors.css('text')}; border: 1px solid {colors.css('text')}; font-weight: 700; }}"
                if button.isChecked() else "")

    def _hover(self, moment: float | None) -> None:
        for chart in self.charts:
            chart.set_hover(moment)
