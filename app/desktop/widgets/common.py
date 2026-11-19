"""Building blocks shared by all pages (cards, tiles, badges, forms, dialogs)."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.desktop.i18n import t
from app.desktop.icons import icon


def polish(widget: QWidget) -> None:
    """Re-apply the style sheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value: Any) -> None:
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    polish(widget)


def label(text: str = "", role: str | None = None, *, wrap: bool = False, selectable: bool = False,
          rich: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    if rich:
        widget.setOpenExternalLinks(True)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text: str = "", *, variant: str | None = None, icon_name: str | None = None,
           on_click: Callable[[], Any] | None = None, tooltip: str | None = None, icon_size: int = 16) -> QPushButton:
    widget = QPushButton(text)
    if variant:
        widget.setProperty("variant", variant)
    if icon_name:
        from app.desktop.theme import current_colors, on_color

        colors = current_colors()
        if variant in {"secondary", "ghost", "link"}:
            tint = colors.color("text")
        elif variant == "danger":
            tint = colors.color("danger")
        else:
            tint = on_color(colors.color("accent"))
        widget.setIcon(icon(icon_name, tint, icon_size))
        widget.setIconSize(QSize(icon_size, icon_size))
    if tooltip:
        widget.setToolTip(tooltip)
    if on_click is not None:
        widget.clicked.connect(lambda _checked=False: on_click())
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def icon_button(icon_name: str, tooltip: str, on_click: Callable[[], Any] | None = None, *, size: int = 18,
                checkable: bool = False, color: QColor | str | None = None) -> QToolButton:
    widget = QToolButton()
    widget.setProperty("variant", "icon")
    widget.setIcon(icon(icon_name, color, size))
    widget.setIconSize(QSize(size, size))
    widget.setToolTip(tooltip)
    widget.setAccessibleName(tooltip)
    widget.setCheckable(checkable)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        widget.clicked.connect(lambda _checked=False: on_click())
    return widget


class Badge(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("badge", True)
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_badge(self, text: str, tone: str | None = None) -> None:
        self.setText(text)
        if tone is not None:
            set_prop(self, "tone", tone)


class Card(QFrame):
    """Rounded panel with an optional header (title, subtitle, action widgets)."""

    def __init__(self, title: str | None = None, subtitle: str | None = None, *, parent: QWidget | None = None,
                 variant: str = "true", margins: tuple[int, int, int, int] = (16, 14, 16, 16), spacing: int = 10) -> None:
        super().__init__(parent)
        self.setProperty("card", variant)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(*margins)
        self._outer.setSpacing(spacing)
        self.header = QWidget()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = label(title or "", "h2")
        self.subtitle_label = label(subtitle or "", "muted", wrap=True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        self.subtitle_label.setVisible(bool(subtitle))
        header_layout.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        header_layout.addLayout(self.actions)
        self.header.setVisible(bool(title) or bool(subtitle))
        self._outer.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(spacing)
        self._outer.addLayout(self.body)

    def set_title(self, title: str, subtitle: str | None = None) -> None:
        self.title_label.setText(title)
        if subtitle is not None:
            self.subtitle_label.setText(subtitle)
            self.subtitle_label.setVisible(bool(subtitle))
        self.header.setVisible(True)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        self.header.setVisible(True)
        return widget

    def add(self, widget: QWidget | QLayout, stretch: int = 0) -> Any:
        if isinstance(widget, QLayout):
            self.body.addLayout(widget, stretch)
        else:
            self.body.addWidget(widget, stretch)
        return widget


class StatTile(QFrame):
    def __init__(self, caption: str, value: str = "-", note: str = "", *, parent: QWidget | None = None,
                 compact: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("tile", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self.caption = label(caption, "muted", wrap=True)
        self.value = label(value, "kpi-small" if compact else "kpi", selectable=True)
        self.note = label(note, "small", wrap=True)
        self.note.setVisible(bool(note))
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def set_value(self, value: Any, note: str | None = None, tone: str | None = None) -> None:
        self.value.setText(str(value if value not in (None, "") else "-"))
        if note is not None:
            self.note.setText(note)
            self.note.setVisible(bool(note))
        if tone is not None:
            set_prop(self, "tone", tone)


class FlashMessage(QLabel):
    """Inline status message (the web GUI's flash/alert box)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("flash", True)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, success: bool = True, *, timeout_ms: int = 0, tone: str | None = None) -> None:
        if not text:
            self.hide()
            return
        self.setText(text)
        set_prop(self, "tone", tone or ("success" if success else "error"))
        self.show()
        if timeout_ms > 0:
            self._timer.start(timeout_ms)
        else:
            self._timer.stop()

    def success(self, text: str, timeout_ms: int = 6000) -> None:
        self.show_message(text, True, timeout_ms=timeout_ms)

    def error(self, text: str) -> None:
        self.show_message(text, False)

    def info(self, text: str, timeout_ms: int = 0) -> None:
        self.show_message(text, True, timeout_ms=timeout_ms, tone="info")


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


def hbox(*widgets: QWidget | QLayout | int | None, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0),
         stretch_last: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for item in widgets:
        if item is None:
            continue
        if isinstance(item, int):
            layout.addStretch(item)
        elif isinstance(item, QLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)
    if stretch_last:
        layout.addStretch(1)
    return layout


def vbox(*widgets: QWidget | QLayout | int | None, spacing: int = 8,
         margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for item in widgets:
        if item is None:
            continue
        if isinstance(item, int):
            layout.addStretch(item)
        elif isinstance(item, QLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)
    return layout


def wrap_layout(layout: QLayout) -> QWidget:
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget


class FlowLayout(QLayout):
    """Left-to-right layout that wraps (chips, button groups, tiles)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()  # see clear_layout(): keep the parent until deletion

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class TileGrid(QWidget):
    """Responsive grid of equally wide tiles (columns follow the width)."""

    def __init__(self, min_tile_width: int = 210, spacing: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_width = min_tile_width
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._tiles: list[QWidget] = []
        self._columns = 0

    def add_tile(self, widget: QWidget) -> QWidget:
        self._tiles.append(widget)
        self._relayout(force=True)
        return widget

    def clear(self) -> None:
        for tile in self._tiles:
            self._grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self._tiles = []
        self._columns = 0

    def tiles(self) -> list[QWidget]:
        return list(self._tiles)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self, force: bool = False) -> None:
        width = max(self.width(), self._min_width)
        columns = max(1, min(len(self._tiles) or 1, width // self._min_width))
        if not force and columns == self._columns:
            return
        self._columns = columns
        for tile in self._tiles:
            self._grid.removeWidget(tile)
        for index, tile in enumerate(self._tiles):
            self._grid.addWidget(tile, index // columns, index % columns)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)


class FormGrid(QWidget):
    """Two-column form (label above field) that collapses to one column."""

    def __init__(self, columns: int = 2, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = max(1, columns)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(10)
        self._next = 0
        self.fields: dict[str, QWidget] = {}
        self.rows: dict[str, QWidget] = {}
        self.captions: dict[str, QLabel] = {}

    def add_field(self, name: str, caption: str, widget: QWidget, *, span: int = 1, help_text: str = "") -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if caption and not isinstance(widget, QCheckBox):
            caption_label = label(caption)
            caption_label.setBuddy(widget)
            layout.addWidget(caption_label)
            self.captions[name] = caption_label
        layout.addWidget(widget)
        if help_text:
            layout.addWidget(label(help_text, "small", wrap=True))
        span = min(max(1, span), self._columns)
        if self._next % self._columns + span > self._columns:
            self._next += self._columns - self._next % self._columns
        row, column = divmod(self._next, self._columns)
        self._grid.addWidget(container, row, column, 1, span)
        self._next += span
        self.fields[name] = widget
        self.rows[name] = container
        for column_index in range(self._columns):
            self._grid.setColumnStretch(column_index, 1)
        return widget

    def add_widget(self, widget: QWidget, *, span: int | None = None) -> QWidget:
        span = self._columns if span is None else min(max(1, span), self._columns)
        if self._next % self._columns + span > self._columns:
            self._next += self._columns - self._next % self._columns
        row, column = divmod(self._next, self._columns)
        self._grid.addWidget(widget, row, column, 1, span)
        self._next += span
        return widget

    def set_row_visible(self, name: str, visible: bool) -> None:
        row = self.rows.get(name)
        if row is not None:
            row.setVisible(visible)


def line_edit(value: Any = "", *, placeholder: str = "", max_length: int | None = None,
              read_only: bool = False) -> QLineEdit:
    widget = QLineEdit(str(value if value is not None else ""))
    if placeholder:
        widget.setPlaceholderText(placeholder)
    if max_length:
        widget.setMaxLength(max_length)
    widget.setReadOnly(read_only)
    return widget


def text_edit(value: Any = "", *, placeholder: str = "", height: int = 80) -> QPlainTextEdit:
    widget = QPlainTextEdit(str(value if value is not None else ""))
    if placeholder:
        widget.setPlaceholderText(placeholder)
    widget.setFixedHeight(height)
    return widget


def combo(options: Iterable[tuple[Any, str]] | Iterable[dict[str, Any]], value: Any = None) -> QComboBox:
    widget = QComboBox()
    set_combo_options(widget, options, value)
    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    widget.setMinimumContentsLength(8)
    return widget


def set_combo_options(widget: QComboBox, options: Iterable[tuple[Any, str]] | Iterable[dict[str, Any]],
                      value: Any = None, *, translate: bool = True) -> None:
    widget.blockSignals(True)
    widget.clear()
    for option in options:
        if isinstance(option, dict):
            option_value = option.get("value")
            option_label = str(option.get("label") or option_value or "")
        else:
            option_value, option_label = option
        widget.addItem(t(option_label) if translate else str(option_label), option_value)
    if value is not None:
        set_combo_value(widget, value)
    widget.blockSignals(False)


def set_combo_value(widget: QComboBox, value: Any) -> bool:
    for index in range(widget.count()):
        if str(widget.itemData(index)) == str(value):
            widget.setCurrentIndex(index)
            return True
    return False


def combo_value(widget: QComboBox) -> Any:
    data = widget.currentData()
    return data if data is not None else widget.currentText()


def checkbox(text: str, checked: Any = False) -> QCheckBox:
    widget = QCheckBox(text)
    widget.setChecked(truthy(checked))
    return widget


def spin(value: Any = 0, minimum: int = 0, maximum: int = 1_000_000, *, suffix: str = "") -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    try:
        widget.setValue(int(float(value)))
    except (TypeError, ValueError):
        widget.setValue(minimum)
    if suffix:
        widget.setSuffix(suffix)
    return widget


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "on", "yes", "y", "t"}


def form_flag(value: bool) -> str | None:
    """Checkbox value as the web form posted it ("1" when checked, None otherwise)."""
    return "1" if value else None


def confirm(parent: QWidget | None, title: str, text: str, *, danger: bool = False,
            confirm_text: str | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    yes = box.addButton(confirm_text or t("Confirm"), QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(t("Cancel"), QMessageBox.ButtonRole.RejectRole)
    if danger:
        yes.setProperty("variant", "danger")
    no.setProperty("variant", "secondary")
    box.setDefaultButton(no if danger else yes)
    box.exec()
    return box.clickedButton() is yes


def info(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Information)
    box.addButton(t("Close"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def error(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Critical)
    box.addButton(t("Close"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


class Page(QWidget):
    """Base class of all pages shown in the main window."""

    title_changed = Signal(str)
    navigate_requested = Signal(str, object)  # (page key, argument)

    page_key = ""
    page_title = ""
    auto_refresh_ms = 0
    scrollable = True

    def __init__(self, context: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.body_widget = QWidget()
        self.body_widget.setObjectName("PageBody")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(20, 18, 20, 20)
        self.body.setSpacing(14)
        if self.scrollable:
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll.setWidget(self.body_widget)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            outer.addWidget(self.scroll)
        else:
            self.scroll = None
            outer.addWidget(self.body_widget)
        # Parented so it can never pop up as a separate window; pages place it in their layout.
        self.flash = FlashMessage(self)
        self.flash.hide()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._timer_tick)
        self._visible_on_screen = False
        self._built = False
        self.current_argument: Any = None

    # Pages build lazily the first time they are shown.
    def ensure_built(self) -> None:
        if not self._built:
            self._built = True
            self.build()

    def build(self) -> None:  # pragma: no cover - overridden
        pass

    def refresh(self) -> None:  # pragma: no cover - overridden
        pass

    def activate(self, argument: Any = None) -> None:
        """Called when the page becomes visible (``argument`` from navigation)."""
        self.ensure_built()
        self._visible_on_screen = True
        # Remembered so the window can reopen the same record after rebuilding pages.
        self.current_argument = argument
        self.open_argument(argument)
        self.refresh()
        if self.auto_refresh_ms > 0:
            self._timer.start(self.auto_refresh_ms)

    def deactivate(self) -> None:
        self._visible_on_screen = False
        self._timer.stop()

    def open_argument(self, argument: Any) -> None:
        pass

    def _timer_tick(self) -> None:
        if self._visible_on_screen and self.isVisible():
            self.refresh()

    def navigate(self, key: str, argument: Any = None) -> None:
        self.navigate_requested.emit(key, argument)

    @property
    def can_edit(self) -> bool:
        return bool(getattr(self.context, "can_edit", True))

    def header(self, title: str, subtitle: str = "", *actions: QWidget) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(title, "page-title"))
        if subtitle:
            titles.addWidget(label(subtitle, "muted", wrap=True))
        layout.addLayout(titles, 1)
        for action in actions:
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
        return widget


class DetailDialog(QDialog):
    """Resizable dialog with a scrollable body and a close button."""

    def __init__(self, title: str, parent: QWidget | None = None, *, width: int = 760, height: int = 620) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_widget = QWidget()
        body_widget.setObjectName("PageBody")
        self.body = QVBoxLayout(body_widget)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(12)
        scroll.setWidget(body_widget)
        outer.addWidget(scroll, 1)
        buttons = QDialogButtonBox()
        close_button = buttons.addButton(t("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        close_button.setProperty("variant", "secondary")
        buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.setContentsMargins(18, 8, 18, 14)
        self.footer = footer
        footer.addStretch(1)
        footer.addWidget(buttons)
        outer.addLayout(footer)


def key_value_grid(rows: Sequence[tuple[str, Any]], *, selectable: bool = True, wrap: bool = True) -> QWidget:
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(6)
    for index, (caption, value) in enumerate(rows):
        caption_label = label(caption, "muted")
        caption_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Captions keep their natural width; the value column takes the rest.
        caption_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        if isinstance(value, QWidget):
            value_widget = value
            compact = value_widget.sizePolicy().horizontalPolicy() in (QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            if compact:
                # Badges and other compact widgets keep their natural width.
                grid.addWidget(value_widget, index, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            else:
                grid.addWidget(value_widget, index, 1)
        else:
            value_widget = label("-" if value in (None, "") else str(value), wrap=wrap, selectable=selectable)
            value_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            grid.addWidget(value_widget, index, 1)
        grid.addWidget(caption_label, index, 0)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    return widget


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # Keep the parent until deleteLater(): setParent(None) would hand the object to Python,
            # which could delete it at once - even while one of its signals is being handled.
            widget.hide()
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)


def elide(text: str, length: int) -> str:
    text = str(text or "")
    return text if len(text) <= length else text[: max(0, length - 1)] + "…"


class ColorSwatch(QWidget):
    def __init__(self, color: QColor, size: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(self.palette().color(QPalette.ColorRole.Mid))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)


def tone_color(tone: str | None) -> QColor:
    from app.desktop.theme import current_colors

    colors = current_colors()
    normalized = str(tone or "").strip().lower()
    if normalized in {"ok", "success", "good", "enabled"}:
        return colors.color("success")
    if normalized in {"warn", "warning", "attention"}:
        return colors.color("warning")
    if normalized in {"error", "danger", "critical", "disabled-error"}:
        return colors.color("danger")
    if normalized in {"accent", "info"}:
        return colors.color("accent")
    return colors.color("muted")


class ToneDot(QWidget):
    """The web GUI's .dashboard-indicator: a small status dot."""

    def __init__(self, tone: str = "neutral", size: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = tone
        self.setFixedSize(size, size)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = tone_color(self.tone)
        halo = QColor(color)
        halo.setAlphaF(0.25)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(self.rect())
        painter.setBrush(color)
        inset = max(1, self.width() // 5)
        painter.drawEllipse(self.rect().adjusted(inset, inset, -inset, -inset))


class ToneLabel(QWidget):
    """Status text with a leading tone dot (status icon + label, never color alone)."""

    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None, *, bold: bool = True) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.dot = ToneDot(tone)
        self.text = QLabel(text)
        if bold:
            font = self.text.font()
            font.setBold(True)
            self.text.setFont(font)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text, 1)

    def set_state(self, text: str, tone: str) -> None:
        self.text.setText(text)
        self.dot.set_tone(tone)


def event_is_hover(event: QEvent) -> bool:
    return event.type() in {QEvent.Type.Enter, QEvent.Type.HoverEnter}


def set_enabled(widgets: Iterable[QWidget | QAbstractButton], enabled: bool) -> None:
    for widget in widgets:
        widget.setEnabled(enabled)


__all__ = [
    "Badge",
    "Card",
    "ColorSwatch",
    "DetailDialog",
    "FlashMessage",
    "FlowLayout",
    "FormGrid",
    "Page",
    "StatTile",
    "TileGrid",
    "button",
    "checkbox",
    "clear_layout",
    "combo",
    "combo_value",
    "confirm",
    "elide",
    "error",
    "form_flag",
    "hbox",
    "icon_button",
    "info",
    "key_value_grid",
    "label",
    "line_edit",
    "polish",
    "separator",
    "set_combo_options",
    "set_combo_value",
    "set_enabled",
    "set_prop",
    "spin",
    "text_edit",
    "truthy",
    "vbox",
    "wrap_layout",
]
