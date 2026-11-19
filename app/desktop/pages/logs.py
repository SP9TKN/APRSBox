"""Event logs page (web: templates/logs.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout

from app.datetime_utils import format_display_datetime
from app.desktop.i18n import t, tf
from app.desktop.tasks import SerialLoader
from app.desktop.widgets.common import Card, Page, combo, combo_value, label, set_combo_value, tone_color
from app.desktop.widgets.table import Column, DataTable

VIEW_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
CONFIGURABLE_MIN_LEVELS = ("INFO", "WARNING", "ERROR")


def configured_min_level() -> str:
    from app.db import get_event_log_min_level, normalize_event_log_level

    level = normalize_event_log_level(get_event_log_min_level())
    return level if level in CONFIGURABLE_MIN_LEVELS else "INFO"


class LogsPage(Page):
    page_key = "logs"
    scrollable = False

    def build(self) -> None:
        card = Card(t("Event Logs"))
        controls = QHBoxLayout()
        controls.addWidget(label(t("Minimum visible log level")))
        self.min_level = configured_min_level()
        self.level = combo([(level, level) for level in VIEW_LEVELS], self.min_level)
        self.level.currentIndexChanged.connect(lambda _index: self._level_changed())
        controls.addWidget(self.level)
        self.visible = label("", "small")
        controls.addWidget(self.visible)
        controls.addStretch(1)
        card.add(controls)
        self.table = DataTable(
            [
                Column("id", t("ID"), width=70),
                Column("created_at", t("Time"), width=170,
                       display=lambda row: format_display_datetime(row.get("created_at"), use_utc=True)),
                Column("level", t("Level"), width=90, foreground=self._level_color),
                Column("category", t("Category"), width=150, tooltip=lambda row: str(row.get("category") or "")),
                Column("message", t("Message"), stretch=True, tooltip=lambda row: str(row.get("message") or "")),
            ],
            sortable=False,
        )
        self.table.set_empty_text(t("No logs available yet."))
        card.add(self.table, 1)
        self.body.addWidget(card, 1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 10000
        self._update_visible()

    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict) and argument.get("min_level"):
            set_combo_value(self.level, str(argument["min_level"]).upper())

    @staticmethod
    def _level_color(row: dict[str, Any]) -> Any:
        level = str(row.get("level") or "")
        if level == "ERROR":
            return tone_color("error")
        if level == "WARNING":
            return tone_color("warn")
        return None

    def _level_changed(self) -> None:
        self.min_level = str(combo_value(self.level) or "INFO")
        self._update_visible()
        self.refresh()

    def _update_visible(self) -> None:
        from app.db import event_log_levels_at_or_above

        levels = event_log_levels_at_or_above(self.min_level)
        self.visible.setText(tf("Visible levels: {levels}", levels=", ".join(levels)))

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> list[dict[str, Any]]:
        from app.services.content import recent_event_logs

        # Runs in a worker thread: read the plain attribute, never the combo box.
        return recent_event_logs(limit=200, min_level=self.min_level)

    def _apply(self, rows: list[dict[str, Any]]) -> None:
        self.table.set_rows(rows or [])
