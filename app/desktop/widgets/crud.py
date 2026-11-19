"""Row action bar used under record tables (edit / delete / test ...)."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from app.desktop.widgets.common import button
from app.desktop.widgets.table import DataTable


class RowActionBar(QWidget):
    """Buttons acting on the selected table row; disabled while nothing is selected."""

    def __init__(self, table: DataTable, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table = table
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._buttons: list[tuple[QPushButton, Callable[[dict[str, Any]], bool] | None]] = []
        table.selection_changed_rows.connect(lambda _rows: self.sync())
        table.proxy.modelReset.connect(self.sync)
        table.proxy.layoutChanged.connect(self.sync)

    def add(self, text: str, icon_name: str, callback: Callable[[dict[str, Any]], Any], *, variant: str | None = "secondary",
            enabled_for: Callable[[dict[str, Any]], bool] | None = None, tooltip: str | None = None) -> QPushButton:
        def run() -> None:
            row = self.table.current_row()
            if row is not None:
                callback(row)

        widget = button(text, variant=variant, icon_name=icon_name, on_click=run, tooltip=tooltip)
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self._buttons.append((widget, enabled_for))
        self.sync()
        return widget

    def sync(self) -> None:
        row = self.table.current_row()
        for widget, enabled_for in self._buttons:
            widget.setEnabled(row is not None and (enabled_for is None or bool(enabled_for(row))))


def tx_log_table(empty_text: str = "") -> DataTable:
    """Outbound job log (web: beacon-log-table on station, objects, bulletins and WX pages)."""
    from PySide6.QtCore import Qt

    from app.datetime_utils import format_display_datetime
    from app.desktop.i18n import t
    from app.desktop.theme import current_colors
    from app.desktop.widgets.common import tone_color
    from app.desktop.widgets.table import Column

    def status_color(row: dict[str, Any]) -> Any:
        status = str(row.get("status") or "")
        return tone_color("ok" if status == "sent" else ("error" if status == "failed" else "warn"))

    def skipped_color(row: dict[str, Any]) -> Any:
        return current_colors().color("muted") if row.get("is_tx_skipped") else None

    table = DataTable(
        [
            Column("display_time", t("Time"), width=150,
                   display=lambda row: format_display_datetime(row.get("display_time")) if row.get("display_time") else "-"),
            Column("kind", t("Type"), width=90, display=lambda row: t(str(row.get("display_kind") or row.get("kind") or ""))),
            Column("status", t("Status"), width=90, display=lambda row: t(str(row.get("status") or "")), foreground=status_color),
            Column("interface_name", t("Interface"), width=140),
            Column("attempt_count", t("Attempts"), width=70, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            Column("last_error", t("Error"), width=200, display=lambda row: row.get("last_error") or "-",
                   tooltip=lambda row: str(row.get("last_error") or "")),
            Column("line", t("Frame"), stretch=True, mono=True, display=lambda row: row.get("line") or "-",
                   tooltip=lambda row: str(row.get("line") or ""), foreground=skipped_color),
        ],
        sortable=False,
    )
    table.set_empty_text(empty_text or t("No records yet."))
    table.setMinimumHeight(240)
    return table


class AsciiTextField(QWidget):
    """Multi-line field limited to printable ASCII with a 'n / max' counter (web: bindAsciiCounter)."""

    def __init__(self, maximum: int, *, placeholder: str = "", height: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import re

        from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout

        from app.desktop.i18n import t
        from app.desktop.widgets.common import label

        self._strip = re.compile(r"[^\x20-\x7E]")
        self.maximum = maximum
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.edit = QPlainTextEdit()
        self.edit.setFixedHeight(height)
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        layout.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.counter = label(f"0 / {maximum}", "small")
        row.addWidget(self.counter)
        self.error = label(t("Only printable ASCII is allowed. National characters are blocked."), "error", wrap=True)
        self.error.hide()
        row.addWidget(self.error, 1)
        layout.addLayout(row)
        self.edit.textChanged.connect(self._changed)

    def _changed(self) -> None:
        text = self.edit.toPlainText()
        filtered = self._strip.sub("", text)[: self.maximum]
        if filtered != text:
            cursor = self.edit.textCursor()
            position = min(cursor.position(), len(filtered))
            self.edit.blockSignals(True)
            self.edit.setPlainText(filtered)
            cursor = self.edit.textCursor()
            cursor.setPosition(position)
            self.edit.setTextCursor(cursor)
            self.edit.blockSignals(False)
            self.error.show()
        self.counter.setText(f"{len(filtered)} / {self.maximum}")

    def text(self) -> str:
        return self.edit.toPlainText()

    def set_text(self, value: str) -> None:
        self.edit.setPlainText(str(value or ""))
        self.error.hide()
        self._changed()
