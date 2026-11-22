"""Traffic log list (web: .traffic-stream rows with per-class colors)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from app.desktop.i18n import t
from app.desktop.theme import MONO_FONT_FAMILY, UI_FONT_FAMILY, current_colors, mix

FRAME_ROLE = Qt.ItemDataRole.UserRole + 10

# row_class -> (palette color key, background percent, bar width)
ROW_STYLES: dict[str, tuple[str, float, int]] = {
    "traffic-log-row-own-beacon-tx": ("traffic-color-own-beacon-tx", 0.20, 3),
    "traffic-log-row-own-beacon-rx": ("traffic-color-own-beacon-rx", 0.09, 3),
    "traffic-log-row-own-wx-tx": ("traffic-color-own-wx-tx", 0.20, 3),
    "traffic-log-row-own-wx-rx": ("traffic-color-own-wx-rx", 0.09, 3),
    "traffic-log-row-own-message-tx": ("traffic-color-own-message-tx", 0.20, 3),
    "traffic-log-row-own-message-rx": ("traffic-color-own-message-rx", 0.09, 3),
    "traffic-log-row-repeated-tx": ("traffic-color-repeated-tx", 0.17, 3),
    "traffic-log-row-proxy-tx": ("traffic-color-proxy-tx", 0.20, 3),
    "traffic-log-row-aprsis-rx": ("traffic-color-aprsis-rx", 0.09, 3),
    "traffic-log-row-aprsis-to-rf-tx": ("traffic-color-aprsis-to-rf-tx", 0.26, 5),
    "traffic-log-row-emergency": ("danger", 0.14, 5),
    "traffic-log-row-remote": ("muted", 0.10, 3),  # another station's receive page (SP9DLM 8080)
}

LEGEND_ENTRIES: tuple[tuple[str, str], ...] = (
    ("traffic-log-row-own-beacon-tx", "Own beacon TX"),
    ("traffic-log-row-own-beacon-rx", "Own beacon echo RX"),
    ("traffic-log-row-own-wx-tx", "Own weather TX"),
    ("traffic-log-row-own-wx-rx", "Own weather echo RX"),
    ("traffic-log-row-own-message-tx", "Own message/query/ACK TX"),
    ("traffic-log-row-own-message-rx", "Own message/query/ACK echo RX"),
    ("traffic-log-row-repeated-tx", "DIGI repeated TX"),
    ("traffic-log-row-proxy-tx", "Shared KISS port TX"),
    ("traffic-log-row-aprsis-rx", "APRS-IS RX"),
    ("traffic-log-row-aprsis-to-rf-tx", "APRS-IS to RF TX"),
    ("traffic-log-row-remote", "Heard by the remote receiver (its receive page)"),
)


def row_style(row_class: str) -> tuple[QColor | None, QColor | None, int]:
    """(bar color, background color, bar width) for a frame's row_class."""
    colors = current_colors()
    classes = str(row_class or "").split()
    for name in reversed(classes):
        style = ROW_STYLES.get(name)
        if style is None:
            continue
        key, percent, width = style
        bar = colors.color(key)
        background = mix(bar, colors.color("panel"), percent)
        return bar, background, width
    return None, None, 3


def legend_color(row_class: str) -> QColor:
    bar, _background, _width = row_style(row_class)
    return bar or current_colors().color("border")


def frame_source_text(frame: dict[str, Any]) -> str:
    parts = [str(frame.get("source") or "")]
    if frame.get("band"):
        parts.append(str(frame.get("band")))
    if frame.get("direction"):
        parts.append(str(frame.get("direction")))
    return " · ".join(part for part in parts if part)


def is_aprsis_to_rf(frame: dict[str, Any]) -> bool:
    return "traffic-log-row-aprsis-to-rf-tx" in str(frame.get("row_class") or "").split()


class TrafficLogModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.frames: list[dict[str, Any]] = []
        self._keys: list[str] = []

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.frames)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.frames):
            return None
        frame = self.frames[index.row()]
        if role == FRAME_ROLE:
            return frame
        if role == Qt.ItemDataRole.DisplayRole:
            return str(frame.get("line") or "")
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{frame.get('timestamp') or ''}\n{frame_source_text(frame)}\n{frame.get('line') or ''}"
        return None

    @staticmethod
    def frame_key(frame: dict[str, Any], seen: dict[str, int]) -> str:
        raw = "|".join(
            str(frame.get(name) or "")
            for name in ("timestamp", "source", "direction", "band", "command", "port", "length", "line", "hex")
        )
        count = seen.get(raw, 0) + 1
        seen[raw] = count
        return f"{raw}#{count}"

    def set_frames(self, frames: list[dict[str, Any]]) -> None:
        seen: dict[str, int] = {}
        keys = [self.frame_key(frame, seen) for frame in frames]
        if keys == self._keys:
            changed = False
            for index, frame in enumerate(frames):
                if frame != self.frames[index]:
                    self.frames[index] = frame
                    changed = True
            if changed and self.frames:
                self.dataChanged.emit(self.index(0), self.index(len(self.frames) - 1))
            return
        self.beginResetModel()
        self.frames = list(frames)
        self._keys = keys
        self.endResetModel()


class TrafficLogDelegate(QStyledItemDelegate):
    ROW_HEIGHT = 26

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.compact = compact
        self.time_width = 150
        self.source_width = 200

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        frame = index.data(FRAME_ROLE) or {}
        colors = current_colors()
        rect = option.rect
        painter.save()
        bar, background, bar_width = row_style(str(frame.get("row_class") or ""))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, colors.color("accent-soft"))
        elif background is not None:
            painter.fillRect(rect, background)
        elif index.row() % 2:
            painter.fillRect(rect, colors.color("panel-alt"))
        if bar is not None:
            painter.fillRect(QRect(rect.left(), rect.top(), bar_width, rect.height()), bar)
        painter.setPen(QPen(colors.color("border"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        text_color = colors.color("text")
        muted = colors.color("muted")
        skipped = "traffic-log-row-skipped" in str(frame.get("row_class") or "")
        mono = QFont(MONO_FONT_FAMILY, 9)
        ui = QFont(UI_FONT_FAMILY, 9)
        x = rect.left() + 10
        y = rect.top()
        height = rect.height()
        painter.setFont(mono)
        painter.setPen(muted)
        painter.drawText(QRect(x, y, self.time_width, height), Qt.AlignmentFlag.AlignVCenter, str(frame.get("timestamp") or ""))
        x += self.time_width
        painter.setFont(ui)
        source = frame_source_text(frame)
        metrics = QFontMetrics(ui)
        source_text = metrics.elidedText(source, Qt.TextElideMode.ElideRight, self.source_width - 8)
        painter.setPen(muted)
        painter.drawText(QRect(x, y, self.source_width, height), Qt.AlignmentFlag.AlignVCenter, source_text)
        if is_aprsis_to_rf(frame):
            badge_font = QFont(UI_FONT_FAMILY, 7)
            badge_font.setBold(True)
            painter.setFont(badge_font)
            badge_metrics = QFontMetrics(badge_font)
            badge_text = "IS → RF"
            badge_x = x + min(metrics.horizontalAdvance(source_text) + 6, self.source_width - badge_metrics.horizontalAdvance(badge_text) - 12)
            badge_rect = QRect(badge_x, y + 5, badge_metrics.horizontalAdvance(badge_text) + 10, height - 10)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.color("traffic-color-aprsis-to-rf-tx"))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QColor("#1a1a1a"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
        x += self.source_width
        painter.setFont(mono)
        painter.setPen(muted if skipped else text_color)
        line_rect = QRect(x, y, rect.right() - x - 8, height)
        link_text = ""
        if frame.get("alert_href"):
            link_text = t("Show alert")
        elif frame.get("alert_record_deleted"):
            link_text = t("Alert record deleted")
        if link_text:
            link_font = QFont(UI_FONT_FAMILY, 9)
            link_font.setBold(bool(frame.get("alert_href")))
            link_width = QFontMetrics(link_font).horizontalAdvance(link_text) + 12
            line_rect.setRight(line_rect.right() - link_width)
            painter.setFont(link_font)
            painter.setPen(colors.color("accent") if frame.get("alert_href") else muted)
            painter.drawText(QRect(line_rect.right() + 8, y, link_width, height), Qt.AlignmentFlag.AlignVCenter, link_text)
            painter.setFont(mono)
            painter.setPen(muted if skipped else text_color)
        line = QFontMetrics(mono).elidedText(str(frame.get("line") or ""), Qt.TextElideMode.ElideRight, line_rect.width())
        painter.drawText(line_rect, Qt.AlignmentFlag.AlignVCenter, line)
        painter.restore()


class TrafficLogView(QListView):
    frame_activated = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model_ = TrafficLogModel(self)
        self.setModel(self.model_)
        self.delegate = TrafficLogDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.doubleClicked.connect(self._activated)
        self.activated.connect(self._activated)

    def set_frames(self, frames: list[dict[str, Any]]) -> None:
        scroll = self.verticalScrollBar().value()
        self.model_.set_frames(frames)
        self.verticalScrollBar().setValue(scroll)

    def _activated(self, index: QModelIndex) -> None:
        frame = index.data(FRAME_ROLE)
        if isinstance(frame, dict):
            self.frame_activated.emit(frame)
