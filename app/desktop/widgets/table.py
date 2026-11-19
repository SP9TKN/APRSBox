"""Table views over lists of dicts (the web GUI's data tables)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from app.desktop.theme import MONO_FONT_FAMILY


@dataclass(slots=True)
class Column:
    key: str
    title: str
    width: int | None = None
    stretch: bool = False
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    display: Callable[[dict[str, Any]], Any] | None = None
    sort_value: Callable[[dict[str, Any]], Any] | None = None
    tooltip: Callable[[dict[str, Any]], str] | None = None
    icon: Callable[[dict[str, Any]], QPixmap | QIcon | None] | None = None
    foreground: Callable[[dict[str, Any]], QColor | None] | None = None
    background: Callable[[dict[str, Any]], QColor | None] | None = None
    mono: bool = False
    bold: Callable[[dict[str, Any]], bool] | None = None
    checkable: bool = False
    secondary: Callable[[dict[str, Any]], str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class DictTableModel(QAbstractTableModel):
    def __init__(self, columns: Sequence[Column], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.rows: list[dict[str, Any]] = []
        self.checked: set[Any] = set()
        self.row_key: Callable[[dict[str, Any]], Any] = lambda row: row.get("id")
        self._mono_font = QFont(MONO_FONT_FAMILY, 9)
        self._bold_font = QFont()
        self._bold_font.setBold(True)

    # Qt model API ---------------------------------------------------------
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            return int(self.columns[section].align)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid() and self.columns[index.column()].checkable:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if column.checkable:
                return None
            value = column.display(row) if column.display else row.get(column.key)
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.CheckStateRole and column.checkable:
            return Qt.CheckState.Checked if self.row_key(row) in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            if column.sort_value is not None:
                return column.sort_value(row)
            value = column.display(row) if column.display else row.get(column.key)
            return value
        if role == Qt.ItemDataRole.UserRole + 1:
            return row
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(column.align)
        if role == Qt.ItemDataRole.ToolTipRole and column.tooltip is not None:
            return column.tooltip(row)
        if role == Qt.ItemDataRole.DecorationRole and column.icon is not None:
            return column.icon(row)
        if role == Qt.ItemDataRole.ForegroundRole and column.foreground is not None:
            color = column.foreground(row)
            return QBrush(color) if color is not None else None
        if role == Qt.ItemDataRole.BackgroundRole and column.background is not None:
            color = column.background(row)
            return QBrush(color) if color is not None else None
        if role == Qt.ItemDataRole.FontRole:
            if column.mono:
                return self._mono_font
            if column.bold is not None and column.bold(row):
                return self._bold_font
        return None

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid() or role != Qt.ItemDataRole.CheckStateRole:
            return False
        if not self.columns[index.column()].checkable:
            return False
        key = self.row_key(self.rows[index.row()])
        state = Qt.CheckState(value) if not isinstance(value, Qt.CheckState) else value
        if state == Qt.CheckState.Checked:
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True

    # helpers ----------------------------------------------------------------
    def set_rows(self, rows: Sequence[dict[str, Any]]) -> None:
        new_rows = list(rows)
        if len(new_rows) == len(self.rows) and new_rows == self.rows:
            return
        self.beginResetModel()
        self.rows = new_rows
        keys = {self.row_key(row) for row in new_rows}
        self.checked &= keys
        self.endResetModel()

    def row_at(self, row_index: int) -> dict[str, Any] | None:
        if 0 <= row_index < len(self.rows):
            return self.rows[row_index]
        return None

    def checked_rows(self) -> list[dict[str, Any]]:
        return [row for row in self.rows if self.row_key(row) in self.checked]

    def set_all_checked(self, checked: bool) -> None:
        if checked:
            self.checked = {self.row_key(row) for row in self.rows}
        else:
            self.checked = set()
        if self.rows:
            for column_index, column in enumerate(self.columns):
                if column.checkable:
                    self.dataChanged.emit(self.index(0, column_index), self.index(len(self.rows) - 1, column_index))


class _SortProxy(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.row_filter: Callable[[dict[str, Any]], bool] | None = None
        self.text_filter = ""
        self.text_fields: Callable[[dict[str, Any]], str] | None = None

    def lessThan(self, left: QModelIndex | QPersistentModelIndex, right: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        left_value = left.data(Qt.ItemDataRole.UserRole)
        right_value = right.data(Qt.ItemDataRole.UserRole)
        if left_value is None:
            return right_value is not None
        if right_value is None:
            return False
        try:
            return left_value < right_value
        except TypeError:
            return str(left_value).casefold() < str(right_value).casefold()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, DictTableModel):
            return True
        row = model.row_at(source_row)
        if row is None:
            return False
        if self.row_filter is not None and not self.row_filter(row):
            return False
        if self.text_filter:
            haystack = self.text_fields(row) if self.text_fields else " ".join(str(value) for value in row.values() if value is not None)
            return self.text_filter in haystack.casefold()
        return True


class TwoLineDelegate(QStyledItemDelegate):
    """Primary text + a muted, smaller secondary line (web: .station-secondary-text / .muted)."""

    def __init__(self, column: Column, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.column = column

    def paint(self, painter: Any, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        from app.desktop.theme import current_colors

        row = index.data(Qt.ItemDataRole.UserRole + 1) or {}
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        primary = opt.text.split("\n", 1)[0]
        secondary = self.column.secondary(row) if self.column.secondary else ""
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        colors = current_colors()
        painter.save()
        rect = option.rect.adjusted(6, 3, -6, -3)
        if opt.icon is not None and not opt.icon.isNull():
            rect.setLeft(rect.left() + opt.decorationSize.width() + 6)
        font = QFont(opt.font)
        if self.column.bold is not None and self.column.bold(row):
            font.setBold(True)
        painter.setFont(font)
        foreground = index.data(Qt.ItemDataRole.ForegroundRole)
        painter.setPen(foreground.color() if isinstance(foreground, QBrush) else colors.color("text"))
        metrics = painter.fontMetrics()
        if secondary:
            top_rect = rect.adjusted(0, 0, 0, -rect.height() // 2)
            painter.drawText(top_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                             metrics.elidedText(primary, Qt.TextElideMode.ElideRight, rect.width()))
            small = QFont(opt.font)
            small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
            painter.setFont(small)
            painter.setPen(colors.color("muted"))
            bottom_rect = rect.adjusted(0, rect.height() // 2, 0, 0)
            painter.drawText(bottom_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                             painter.fontMetrics().elidedText(secondary, Qt.TextElideMode.ElideRight, rect.width()))
        else:
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             metrics.elidedText(primary, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()


class DataTable(QTableView):
    """Sortable, filterable table; emits the row dict on activation."""

    row_activated = Signal(dict)
    selection_changed_rows = Signal(list)

    def __init__(self, columns: Sequence[Column], parent: QWidget | None = None, *, sortable: bool = True,
                 row_height: int = 28, alternating: bool = True) -> None:
        super().__init__(parent)
        self.source = DictTableModel(columns, self)
        self.proxy = _SortProxy(self)
        self.proxy.setSourceModel(self.source)
        self.setModel(self.proxy)
        self.setSortingEnabled(sortable)
        self.setAlternatingRowColors(alternating)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        for index, column in enumerate(columns):
            if column.secondary is not None:
                self.setItemDelegateForColumn(index, TwoLineDelegate(column, self))
            if column.stretch:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
            elif column.width:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(index, column.width)
            else:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self.doubleClicked.connect(self._on_activated)
        self.activated.connect(self._on_activated)
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._last_activated: int | None = None
        # Painted in the viewport while the table has no (visible) rows.
        self.empty_text = ""

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text
        self.viewport().update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if self.empty_text and self.proxy.rowCount() == 0:
            from app.desktop.theme import current_colors

            painter = QPainter(self.viewport())
            painter.setPen(current_colors().color("muted"))
            rect = self.viewport().rect().adjusted(12, 10, -12, -10)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), self.empty_text)
            painter.end()

    def set_rows(self, rows: Sequence[dict[str, Any]]) -> None:
        selected_key = None
        current = self.current_row()
        if current is not None:
            selected_key = self.source.row_key(current)
        vertical = self.verticalScrollBar().value()
        self.source.set_rows(rows)
        if selected_key is not None:
            self.select_key(selected_key, scroll=False)
        self.verticalScrollBar().setValue(vertical)

    def set_row_key(self, function: Callable[[dict[str, Any]], Any]) -> None:
        self.source.row_key = function

    def _change_filter(self, apply: Callable[[], None]) -> None:
        # Qt >= 6.10 replaced invalidateFilter() with begin/endFilterChange().
        if hasattr(self.proxy, "beginFilterChange"):
            self.proxy.beginFilterChange()
            apply()
            self.proxy.endFilterChange()
        else:  # pragma: no cover - older Qt
            apply()
            self.proxy.invalidateFilter()

    def set_filter(self, predicate: Callable[[dict[str, Any]], bool] | None) -> None:
        def apply() -> None:
            self.proxy.row_filter = predicate

        self._change_filter(apply)

    def set_text_filter(self, text: str, fields: Callable[[dict[str, Any]], str] | None = None) -> None:
        def apply() -> None:
            self.proxy.text_filter = str(text or "").strip().casefold()
            if fields is not None:
                self.proxy.text_fields = fields

        self._change_filter(apply)

    def visible_count(self) -> int:
        return self.proxy.rowCount()

    def current_row(self) -> dict[str, Any] | None:
        index = self.currentIndex()
        if not index.isValid():
            return None
        source_index = self.proxy.mapToSource(index)
        return self.source.row_at(source_index.row())

    def selected_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index in self.selectionModel().selectedRows():
            source_index = self.proxy.mapToSource(index)
            row = self.source.row_at(source_index.row())
            if row is not None:
                rows.append(row)
        return rows

    def select_key(self, key: Any, *, scroll: bool = True) -> bool:
        for row_index, row in enumerate(self.source.rows):
            if self.source.row_key(row) == key:
                proxy_index = self.proxy.mapFromSource(self.source.index(row_index, 0))
                if proxy_index.isValid():
                    self.selectRow(proxy_index.row())
                    if scroll:
                        self.scrollTo(proxy_index)
                    return True
        return False

    def _on_activated(self, index: QModelIndex) -> None:
        source_index = self.proxy.mapToSource(index)
        row = self.source.row_at(source_index.row())
        if row is not None:
            self.row_activated.emit(row)

    def _on_selection_changed(self, *_args: Any) -> None:
        self.selection_changed_rows.emit(self.selected_rows())

    def fit_height(self, max_rows: int = 12, min_rows: int = 2) -> None:
        rows = max(min_rows, min(max_rows, self.proxy.rowCount()))
        height = self.horizontalHeader().height() + rows * self.verticalHeader().defaultSectionSize() + 6
        self.setMinimumHeight(height)
        self.setMaximumHeight(height if self.proxy.rowCount() <= max_rows else 16777215)
