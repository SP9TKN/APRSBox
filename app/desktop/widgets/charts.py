"""Native charts (web GUI used Chart.js).

Follows the dataviz conventions: 2px round lines, ~10% area wash, hairline
recessive grid, one y-axis with clean ticks, legend for >= 2 series plus
selective end labels, text in text colors (never series colors), and a
crosshair that snaps to the nearest X with a tooltip listing every series.
APRSBox's drag-to-zoom / double-click-reset behavior is kept.

Series colors were validated with the dataviz palette validator (CVD and
normal-vision separation, contrast) for both themes: three hue slots plus a
neutral ink series, matching the web chart's neutral "RX/all frames" line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.desktop.theme import UI_FONT_FAMILY, current_colors

# Validated categorical slots (dark / light) in fixed order: blue, orange, aqua,
# yellow.  The four hues pass the adjacent CVD (>= 8.4) and normal-vision
# (>= 19.8) gates on both chart surfaces; in light mode orange/aqua/yellow sit
# below 3:1 contrast, so charts keep the legend, end labels and tooltips.
SERIES_SLOTS: dict[str, tuple[str, str]] = {
    "blue": ("#3987e5", "#2a78d6"),
    "orange": ("#d95926", "#eb6834"),
    "aqua": ("#199e70", "#1baf7a"),
    "yellow": ("#c98500", "#eda100"),
}
SLOT_ORDER = ("blue", "orange", "aqua", "yellow")


def slot_color(slot: str) -> QColor:
    colors = current_colors()
    if slot == "ink":
        return colors.color("text")
    pair = SERIES_SLOTS.get(slot, SERIES_SLOTS["blue"])
    return QColor(pair[0] if colors.is_dark else pair[1])


def _round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def format_number(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}".replace(",", " ")
    return f"{int(round(value)):,}".replace(",", " ")


def nice_ticks(maximum: float, count: int = 5) -> list[float]:
    if maximum <= 0 or not math.isfinite(maximum):
        return [0.0, 1.0]
    raw_step = maximum / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step >= raw_step:
            break
    top = math.ceil(maximum / step) * step
    ticks = []
    value = 0.0
    while value <= top + step * 0.001:
        ticks.append(round(value, 10))
        value += step
    return ticks


@dataclass
class Series:
    key: str
    label: str
    values: list[float | None]
    slot: str = "blue"
    fill: bool = False
    visible: bool = True
    color: QColor | None = None

    def resolved_color(self) -> QColor:
        return QColor(self.color) if self.color is not None else slot_color(self.slot)


class LineChart(QWidget):
    zoom_changed = Signal(int, int)

    def __init__(self, parent: QWidget | None = None, *, min_height: int = 240, show_legend: bool = True,
                 zoomable: bool = True) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.labels: list[str] = []
        self.tooltip_labels: list[str] = []
        self.series: list[Series] = []
        self.show_legend = show_legend
        self.zoomable = zoomable
        self.view_start = 0
        self.view_end = 0
        self.hover_index: int | None = None
        self._drag_start_x: float | None = None
        self._drag_current_x: float | None = None
        self.loading = False
        self.empty_text = ""
        self._legend_rects: list[tuple[QRectF, Series]] = []

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(640, 300)

    # ---------------------------------------------------------------- data
    def set_data(self, labels: Sequence[str], series: Sequence[Series], *, tooltip_labels: Sequence[str] | None = None,
                 keep_zoom: bool = False) -> None:
        previous_visibility = {item.key: item.visible for item in self.series}
        self.labels = list(labels)
        self.tooltip_labels = list(tooltip_labels or labels)
        self.series = list(series)
        for item in self.series:
            if item.key in previous_visibility:
                item.visible = previous_visibility[item.key]
        count = len(self.labels)
        if not keep_zoom or self.view_end <= self.view_start or self.view_end > count - 1:
            self.view_start = 0
            self.view_end = max(0, count - 1)
        self.hover_index = None
        self.loading = False
        self.update()

    def set_loading(self, loading: bool) -> None:
        self.loading = loading
        self.update()

    def reset_zoom(self) -> None:
        self.view_start = 0
        self.view_end = max(0, len(self.labels) - 1)
        self.zoom_changed.emit(self.view_start, self.view_end)
        self.update()

    def set_zoom(self, start: int, end: int) -> None:
        count = len(self.labels)
        if count < 2:
            return
        start = max(0, min(count - 1, int(start)))
        end = max(0, min(count - 1, int(end)))
        if end - start < 1:
            return
        self.view_start, self.view_end = start, end
        self.update()

    # ------------------------------------------------------------ geometry
    def _legend_layout(self, metrics: QFontMetrics) -> list[QRectF]:
        """Legend entry rectangles, wrapped onto as many rows as the width needs."""
        rects: list[QRectF] = []
        x, y = 56.0, 6.0
        right = max(120.0, self.width() - 8.0)
        for item in self.series:
            width = 22 + metrics.horizontalAdvance(item.label) + 8
            if rects and x + width > right:
                x, y = 56.0, y + 20.0
            rects.append(QRectF(x, y, width, 18))
            x += width + 14
        return rects

    def _plot_rect(self) -> QRectF:
        legend_height = 6
        if self.show_legend and len(self.series) > 1:
            rects = self._legend_layout(QFontMetrics(QFont(UI_FONT_FAMILY, 8)))
            legend_height = int(rects[-1].bottom()) + 2 if rects else 26
        left = 56
        return QRectF(left, legend_height + 8, max(10, self.width() - left - 64), max(10, self.height() - legend_height - 8 - 30))

    def _visible_series(self) -> list[Series]:
        return [item for item in self.series if item.visible]

    def _y_max(self) -> float:
        values = [
            value
            for item in self._visible_series()
            for value in item.values[self.view_start:self.view_end + 1]
            if value is not None
        ]
        return max(values) if values else 0.0

    def _x_for(self, index: int, rect: QRectF) -> float:
        span = max(1, self.view_end - self.view_start)
        return rect.left() + (index - self.view_start) / span * rect.width()

    def _index_for_x(self, x: float, rect: QRectF) -> int:
        span = max(1, self.view_end - self.view_start)
        ratio = (x - rect.left()) / max(1.0, rect.width())
        return int(round(self.view_start + ratio * span))

    # ------------------------------------------------------------- painting
    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        colors = current_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = colors.color("text")
        muted = colors.color("muted")
        grid = colors.color("border")
        font = QFont(UI_FONT_FAMILY, 8)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        rect = self._plot_rect()
        if self.loading:
            painter.setOpacity(0.55)
        self._paint_legend(painter, metrics, text)
        if len(self.labels) < 1 or not self.series:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            painter.end()
            return
        ticks = nice_ticks(self._y_max())
        top_value = ticks[-1] if ticks[-1] > 0 else 1.0

        def y_for(value: float) -> float:
            return rect.bottom() - (value / top_value) * rect.height()

        painter.setPen(QPen(grid, 1))
        for tick in ticks:
            y = y_for(tick)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(muted)
            painter.drawText(QRectF(0, y - 8, rect.left() - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             format_number(tick))
            painter.setPen(QPen(grid, 1))
        # X labels: ~7 evenly spaced.
        count = self.view_end - self.view_start + 1
        step = max(1, math.ceil(count / 7))
        painter.setPen(muted)
        for index in range(self.view_start, self.view_end + 1, step):
            x = self._x_for(index, rect)
            label = self.labels[index] if index < len(self.labels) else ""
            width = metrics.horizontalAdvance(label) + 6
            painter.drawText(QRectF(x - width / 2, rect.bottom() + 6, width, 16), Qt.AlignmentFlag.AlignCenter, label)
        # Series (area wash first, then lines).
        end_labels: list[tuple[float, str, QColor]] = []
        for item in self._visible_series():
            color = item.resolved_color()
            path = QPainterPath()
            started = False
            last_point: QPointF | None = None
            last_value: float | None = None
            for index in range(self.view_start, self.view_end + 1):
                value = item.values[index] if index < len(item.values) else None
                if value is None:
                    started = False
                    continue
                point = QPointF(self._x_for(index, rect), y_for(value))
                if not started:
                    path.moveTo(point)
                    started = True
                else:
                    path.lineTo(point)
                last_point = point
                last_value = value
            if item.fill and not path.isEmpty():
                area = QPainterPath(path)
                area.lineTo(QPointF(path.currentPosition().x(), rect.bottom()))
                area.lineTo(QPointF(path.elementAt(0).x, rect.bottom()))
                area.closeSubpath()
                wash = QColor(color)
                wash.setAlphaF(0.10)
                painter.fillPath(area, wash)
            pen = QPen(color, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            if last_point is not None and last_value is not None:
                surface = colors.color("panel")
                painter.setPen(QPen(surface, 2))
                painter.setBrush(color)
                painter.drawEllipse(last_point, 4, 4)
                end_labels.append((last_point.y(), format_number(last_value), color))
        # Direct end labels, skipped when they would collide (legend + tooltip remain).
        end_labels.sort(key=lambda item: item[0])
        previous_y = -1000.0
        painter.setPen(text)
        for y, value_text, _color in end_labels:
            if y - previous_y < 13:
                continue
            painter.drawText(QRectF(rect.right() + 8, y - 8, 60, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value_text)
            previous_y = y
        # Drag-to-zoom band.
        if self._drag_start_x is not None and self._drag_current_x is not None:
            band = QRectF(min(self._drag_start_x, self._drag_current_x), rect.top(),
                          abs(self._drag_current_x - self._drag_start_x), rect.height())
            shade = colors.color("accent")
            shade.setAlphaF(0.15)
            painter.fillRect(band, shade)
        # Crosshair + tooltip.
        if self.hover_index is not None and self.view_start <= self.hover_index <= self.view_end:
            self._paint_crosshair(painter, rect, y_for, metrics)
        painter.end()

    def _paint_legend(self, painter: QPainter, metrics: QFontMetrics, text: QColor) -> None:
        self._legend_rects = []
        if not self.show_legend or len(self.series) < 2:
            return
        for item, area in zip(self.series, self._legend_layout(metrics)):
            x, y = area.left(), area.top()
            label_width = metrics.horizontalAdvance(item.label)
            color = item.resolved_color()
            if not item.visible:
                color.setAlphaF(0.3)
            painter.setPen(_round_pen(color, 2.5))
            painter.drawLine(QPointF(x + 2, y + 9), QPointF(x + 16, y + 9))
            painter.setPen(text if item.visible else current_colors().color("muted"))
            painter.drawText(QRectF(x + 22, y, label_width + 4, 18), Qt.AlignmentFlag.AlignVCenter, item.label)
            self._legend_rects.append((area, item))

    def _paint_crosshair(self, painter: QPainter, rect: QRectF, y_for: Any, metrics: QFontMetrics) -> None:
        colors = current_colors()
        index = int(self.hover_index or 0)
        x = self._x_for(index, rect)
        painter.setPen(QPen(colors.color("muted"), 1))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        rows: list[tuple[str, str, QColor]] = []
        for item in self._visible_series():
            value = item.values[index] if index < len(item.values) else None
            color = item.resolved_color()
            if value is not None:
                painter.setPen(QPen(colors.color("panel"), 2))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(x, y_for(value)), 4, 4)
            rows.append((format_number(value), item.label, color))
        title = self.tooltip_labels[index] if index < len(self.tooltip_labels) else ""
        bold = QFont(UI_FONT_FAMILY, 9)
        bold.setBold(True)
        normal = QFont(UI_FONT_FAMILY, 8)
        bold_metrics = QFontMetrics(bold)
        width = max([metrics.horizontalAdvance(title)] + [bold_metrics.horizontalAdvance(value) + metrics.horizontalAdvance(label) + 40 for value, label, _ in rows]) + 20
        height = 22 + 18 * len(rows)
        box_x = x + 12 if x + 12 + width < self.width() else x - 12 - width
        box = QRectF(box_x, rect.top() + 4, width, height)
        painter.setPen(QPen(colors.color("border"), 1))
        painter.setBrush(colors.color("panel-emphasis"))
        painter.drawRoundedRect(box, 6, 6)
        painter.setFont(normal)
        painter.setPen(colors.color("muted"))
        painter.drawText(QRectF(box.left() + 10, box.top() + 4, width - 20, 16), Qt.AlignmentFlag.AlignVCenter, title)
        for row_index, (value, label, color) in enumerate(rows):
            y = box.top() + 22 + row_index * 18
            painter.setPen(_round_pen(color, 2.5))
            painter.drawLine(QPointF(box.left() + 10, y + 9), QPointF(box.left() + 22, y + 9))
            painter.setFont(bold)
            painter.setPen(colors.color("text"))
            value_width = bold_metrics.horizontalAdvance(value)
            painter.drawText(QRectF(box.left() + 28, y, value_width + 4, 18), Qt.AlignmentFlag.AlignVCenter, value)
            painter.setFont(normal)
            painter.setPen(colors.color("muted"))
            painter.drawText(QRectF(box.left() + 34 + value_width, y, width, 18), Qt.AlignmentFlag.AlignVCenter, label)

    # --------------------------------------------------------------- events
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        rect = self._plot_rect()
        x = event.position().x()
        if self._drag_start_x is not None:
            self._drag_current_x = max(rect.left(), min(rect.right(), x))
        if rect.adjusted(-4, 0, 4, 0).contains(event.position()) and self.labels:
            index = max(self.view_start, min(self.view_end, self._index_for_x(x, rect)))
            if index != self.hover_index or self._drag_start_x is not None:
                self.hover_index = index
                self.update()
        elif self.hover_index is not None:
            self.hover_index = None
            self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self.hover_index = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        for area, item in self._legend_rects:
            if area.contains(event.position()):
                if sum(1 for series in self.series if series.visible) > 1 or not item.visible:
                    item.visible = not item.visible
                    self.update()
                return
        rect = self._plot_rect()
        if self.zoomable and event.button() == Qt.MouseButton.LeftButton and rect.contains(event.position()):
            self._drag_start_x = event.position().x()
            self._drag_current_x = self._drag_start_x

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_start_x is None:
            return
        rect = self._plot_rect()
        start_x, end_x = sorted((self._drag_start_x, self._drag_current_x or self._drag_start_x))
        self._drag_start_x = None
        self._drag_current_x = None
        if end_x - start_x >= 8:
            start = self._index_for_x(start_x, rect)
            end = self._index_for_x(end_x, rect)
            if end - start >= 1:
                self.view_start, self.view_end = max(0, start), min(len(self.labels) - 1, end)
                self.zoom_changed.emit(self.view_start, self.view_end)
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self.zoomable:
            self.reset_zoom()


@dataclass
class Bar:
    label: str
    value: float
    note: str = ""
    payload: Any = None
    value_text: str = ""

    def display_value(self) -> str:
        return self.value_text or format_number(self.value)


class BarList(QWidget):
    """Horizontal bars for ranked lists (<= 24px thick, 4px rounded data-end)."""

    bar_clicked = Signal(object)

    BAR_HEIGHT = 16
    ROW_HEIGHT = 26

    def __init__(self, parent: QWidget | None = None, *, slot: str = "blue") -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.bars: list[Bar] = []
        self.slot = slot
        self.hover: int | None = None
        self.empty_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_bars(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars)
        self.setFixedHeight(max(40, len(self.bars) * self.ROW_HEIGHT + 8))
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        colors = current_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(UI_FONT_FAMILY, 9)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        if not self.bars:
            painter.setPen(colors.color("muted"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        label_width = min(max(metrics.horizontalAdvance(bar.label) for bar in self.bars) + 12, int(self.width() * 0.4))
        value_width = max(metrics.horizontalAdvance(bar.display_value()) for bar in self.bars) + 14
        maximum = max((bar.value for bar in self.bars), default=0) or 1
        track_width = max(10, self.width() - label_width - value_width - 8)
        color = slot_color(self.slot)
        for index, bar in enumerate(self.bars):
            top = 4 + index * self.ROW_HEIGHT
            if index == self.hover:
                painter.fillRect(QRectF(0, top, self.width(), self.ROW_HEIGHT), colors.color("hover-layer"))
            painter.setPen(colors.color("text"))
            painter.drawText(QRectF(4, top, label_width - 8, self.ROW_HEIGHT), Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(bar.label, Qt.TextElideMode.ElideRight, label_width - 8))
            width = max(2.0, track_width * (bar.value / maximum))
            bar_rect = QRectF(label_width, top + (self.ROW_HEIGHT - self.BAR_HEIGHT) / 2, width, self.BAR_HEIGHT)
            path = QPainterPath()
            radius = min(4.0, width / 2)
            path.moveTo(bar_rect.left(), bar_rect.top())
            path.lineTo(bar_rect.right() - radius, bar_rect.top())
            path.quadTo(bar_rect.right(), bar_rect.top(), bar_rect.right(), bar_rect.top() + radius)
            path.lineTo(bar_rect.right(), bar_rect.bottom() - radius)
            path.quadTo(bar_rect.right(), bar_rect.bottom(), bar_rect.right() - radius, bar_rect.bottom())
            path.lineTo(bar_rect.left(), bar_rect.bottom())
            path.closeSubpath()
            fill = QColor(color)
            if self.hover is not None and index != self.hover:
                fill.setAlphaF(0.75)
            painter.fillPath(path, fill)
            painter.setPen(colors.color("text"))
            painter.drawText(QRectF(bar_rect.right() + 6, top, value_width, self.ROW_HEIGHT), Qt.AlignmentFlag.AlignVCenter,
                             bar.display_value())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        index = int((event.position().y() - 4) // self.ROW_HEIGHT)
        index = index if 0 <= index < len(self.bars) else None
        if index != self.hover:
            self.hover = index
            self.update()
        if index is not None:
            bar = self.bars[index]
            self.setToolTip(f"{bar.display_value()}  {bar.label}" + (f"\n{bar.note}" if bar.note else ""))
            self.setCursor(Qt.CursorShape.PointingHandCursor if bar.payload is not None else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self.hover = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self.hover is not None and 0 <= self.hover < len(self.bars):
            self.bar_clicked.emit(self.bars[self.hover].payload)


# Propagation index levels W0..W5 (web: band_condition.html chartColors).
CONDITION_COLORS = {0: "#c97a7a", 1: "#d59a62", 2: "#7290ad", 3: "#74ad87", 4: "#59bd77", 5: "#2ce06a"}


class IndexHistoryChart(QWidget):
    """Stepped hourly W0-W5 history with a provisional current-hour point."""

    def __init__(self, parent: QWidget | None = None, *, min_height: int = 220) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.labels: list[str] = []
        self.indexes: list[int | None] = []
        self.provisional: list[int | None] = []
        self.visible_hours = 24
        self.hover_index: int | None = None
        self.empty_text = ""
        self.value_label = "Propagation index"
        self.provisional_label = "Current hour (provisional)"

    def set_data(self, labels: Sequence[str], indexes: Sequence[int | None], provisional: Sequence[int | None]) -> None:
        self.labels = list(labels)
        self.indexes = list(indexes)
        self.provisional = list(provisional)
        self.hover_index = None
        self.update()

    def set_visible_hours(self, hours: int) -> None:
        self.visible_hours = max(1, int(hours))
        self.hover_index = None
        self.update()

    def _start(self) -> int:
        return max(0, len(self.labels) - self.visible_hours)

    def _plot_rect(self) -> QRectF:
        return QRectF(44, 10, max(10, self.width() - 44 - 16), max(10, self.height() - 10 - 30))

    @staticmethod
    def _format_label(value: str, *, with_year: bool = False) -> str:
        from datetime import datetime

        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
        return parsed.strftime("%d.%m.%y %H:%M" if with_year else "%d.%m %H:%M")

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        colors = current_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(UI_FONT_FAMILY, 8)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        muted = colors.color("muted")
        start = self._start()
        labels = self.labels[start:]
        values = self.indexes[start:]
        provisional = self.provisional[start:]
        if not labels or not any(value is not None for value in values + provisional):
            painter.setPen(muted)
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self.empty_text)
            painter.end()
            return
        rect = self._plot_rect()

        def y_for(level: float) -> float:
            return rect.bottom() - (level / 5.0) * rect.height()

        count = len(labels)

        def x_for(index: int) -> float:
            return rect.left() + (rect.width() * index / max(1, count - 1) if count > 1 else rect.width() / 2)

        for level in range(6):
            y = y_for(level)
            painter.setPen(QPen(colors.color("border"), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(muted)
            painter.drawText(QRectF(0, y - 8, rect.left() - 8, 16), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                             f"W{level}")
        ticks = min(8, count)
        for tick in range(ticks):
            index = round(tick * (count - 1) / max(1, ticks - 1)) if ticks > 1 else 0
            text = self._format_label(labels[index])
            x = x_for(index)
            width = metrics.horizontalAdvance(text) + 4
            left = min(max(rect.left() - 10, x - width / 2), rect.right() + 10 - width)
            painter.drawText(QRectF(left, rect.bottom() + 6, width, 18), int(Qt.AlignmentFlag.AlignCenter), text)

        # Area wash and stepped line, segments coloured by the level they reach.
        wash = QColor(CONDITION_COLORS[4])
        wash.setAlphaF(0.10)
        previous: tuple[float, float, int] | None = None
        for index, value in enumerate(values):
            if value is None:
                previous = None
                continue
            x, y = x_for(index), y_for(value)
            if previous is not None:
                px, py, _plevel = previous
                painter.fillRect(QRectF(px, min(py, rect.bottom()), x - px, rect.bottom() - py), wash)
                painter.setPen(_round_pen(QColor(CONDITION_COLORS.get(value, CONDITION_COLORS[2])), 1.6))
                painter.drawLine(QPointF(px, py), QPointF(x, py))
                painter.drawLine(QPointF(x, py), QPointF(x, y))
            previous = (x, y, value)
        draw_points = count <= 24 * 31
        ring = QPen(colors.color("text"), 1)
        for index, value in enumerate(values):
            if value is None or not (draw_points or index == self.hover_index):
                continue
            painter.setPen(ring)
            painter.setBrush(QColor(CONDITION_COLORS.get(value, CONDITION_COLORS[2])))
            radius = 4.0 if index == self.hover_index else 2.2
            painter.drawEllipse(QPointF(x_for(index), y_for(value)), radius, radius)
        for index, value in enumerate(provisional):
            if value is None:
                continue
            painter.setPen(QPen(colors.color("text"), 2))
            painter.setBrush(QColor(CONDITION_COLORS.get(value, CONDITION_COLORS[2])))
            painter.drawEllipse(QPointF(x_for(index), y_for(value)), 5.0, 5.0)
        if self.hover_index is not None and 0 <= self.hover_index < count:
            x = x_for(self.hover_index)
            painter.setPen(QPen(muted, 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        start = self._start()
        count = len(self.labels) - start
        if count <= 0:
            return
        rect = self._plot_rect()
        ratio = (event.position().x() - rect.left()) / max(1.0, rect.width())
        index = int(round(min(1.0, max(0.0, ratio)) * max(0, count - 1)))
        if index != self.hover_index:
            self.hover_index = index
            self.update()
        lines = [self._format_label(self.labels[start + index], with_year=True)]
        value = self.indexes[start + index] if start + index < len(self.indexes) else None
        if value is not None:
            lines.append(f"{self.value_label}: W{value}")
        pending = self.provisional[start + index] if start + index < len(self.provisional) else None
        if pending is not None:
            lines.append(f"{self.provisional_label}: W{pending}")
        self.setToolTip("\n".join(lines))

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self.hover_index = None
        self.update()
