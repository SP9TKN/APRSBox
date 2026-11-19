"""Band Condition page (web: templates/band_condition.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QProgressBar, QToolButton, QVBoxLayout, QWidget

from app.desktop.i18n import t
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors, mix
from app.desktop.widgets.charts import CONDITION_COLORS, IndexHistoryChart
from app.desktop.widgets.common import Badge, Card, Page, button, clear_layout, label

LEVELS = [
    (5, "Very strong opening"),
    (4, "Strong opening"),
    (3, "Moderate opening"),
    (2, "Normal conditions"),
    (1, "Degraded propagation"),
    (0, "Severely degraded"),
]
TONE_COLORS = {
    "excellent": CONDITION_COLORS[5],
    "good": CONDITION_COLORS[4],
    "noticeable": CONDITION_COLORS[3],
    "neutral": CONDITION_COLORS[2],
    "caution": CONDITION_COLORS[1],
    "learning": CONDITION_COLORS[1],
    "bad": CONDITION_COLORS[0],
}
HISTORY_RANGES = [(24, "24h"), (168, "7d"), (720, "30d"), (8760, "365d")]


def distance_km(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f} km"
    except (TypeError, ValueError):
        return "—"


def _stats_grid(rows: list[tuple[str, Any]], columns: int = 2) -> QWidget:
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(8)
    for index, (caption, value) in enumerate(rows):
        cell = QVBoxLayout()
        cell.setSpacing(1)
        cell.addWidget(label(caption, "small", wrap=True))
        cell.addWidget(label(str(value), "h3"))
        grid.addLayout(cell, index // columns, index % columns)
    for column in range(columns):
        grid.setColumnStretch(column, 1)
    return widget


def _progress(value: Any) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    try:
        bar.setValue(int(value or 0))
    except (TypeError, ValueError):
        bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    return bar


class IndexPanel(QFrame):
    """Big W-index, label and summary, plus the W5..W0 legend (web: .band-condition-index)."""

    def __init__(self, item: dict[str, Any]) -> None:
        super().__init__()
        colors = current_colors()
        tone = QColor(TONE_COLORS.get(str(item.get("diagnosis_tone") or "learning"), CONDITION_COLORS[1]))
        background = mix(tone, colors.color("panel-alt"), 0.16 if colors.is_dark else 0.12)
        border = mix(tone, colors.color("panel"), 0.45)
        self.setObjectName("IndexPanel")
        self.setStyleSheet(f"QFrame#IndexPanel {{ background: {background.name()}; border: 1px solid {border.name()}; border-radius: 8px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(18)
        value_box = QVBoxLayout()
        value_box.setSpacing(0)
        caption = label(t("Propagation index").upper(), "eyebrow")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_box.addWidget(caption)
        index = item.get("condition_index")
        value = label("W–" if index is None else f"W{index}")
        # The application style sheet sets label fonts, so size this one the same way.
        value.setStyleSheet("font-size: 40pt; font-weight: 700;")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_box.addWidget(value)
        value_box.addStretch(1)
        layout.addLayout(value_box)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        title = label(t(item.get("label") or ""), "h2", wrap=True)
        copy.addWidget(title)
        copy.addWidget(label(t(item.get("diagnosis_summary") or ""), None, wrap=True))
        copy.addStretch(1)
        layout.addLayout(copy, 1)
        legend_frame = QFrame()
        legend_frame.setObjectName("IndexLegend")
        legend_frame.setStyleSheet(f"QFrame#IndexLegend {{ border: none; border-left: 1px solid {colors.css('border')}; }}")
        legend = QVBoxLayout(legend_frame)
        legend.setContentsMargins(14, 0, 0, 0)
        legend.setSpacing(2)
        for level, caption_text in LEVELS:
            row = _legend_row(level, t(caption_text), active=index == level)
            legend.addWidget(row)
        layout.addWidget(legend_frame)


def _legend_row(level: int, caption: str, *, active: bool) -> QWidget:
    colors = current_colors()
    color = QColor(CONDITION_COLORS[level])
    row = QFrame()
    row.setObjectName("LegendRow")
    if active:
        row.setStyleSheet(
            f"QFrame#LegendRow {{ background: {mix(color, colors.color('panel'), 0.16).name()};"
            f" border: 1px solid {mix(color, colors.color('panel'), 0.5).name()}; border-radius: 4px; }}"
        )
    else:
        row.setStyleSheet("QFrame#LegendRow { border: 1px solid transparent; }")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(6, 2, 6, 2)
    layout.setSpacing(6)
    code = label(f"W{level}")
    code.setStyleSheet(f"color: {color.name()}; font-weight: 700;")
    layout.addWidget(code)
    text = label(caption, None if active else "small")
    layout.addWidget(text, 1)
    return row


class BandInterfacePanel(QWidget):
    def __init__(self, page: "BandConditionPage", interface_id: int) -> None:
        super().__init__()
        self.page = page
        self.interface_id = interface_id
        self.history_hours: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        self.main_card = Card()
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.band_label = label("", "eyebrow")
        self.name_label = label("", "h2")
        titles.addWidget(self.band_label)
        titles.addWidget(self.name_label)
        heading.addLayout(titles, 1)
        self.status = Badge("", "neutral")
        heading.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        self.main_card.add(heading)
        self.index_host = QVBoxLayout()
        self.main_card.add(self.index_host)
        confidence = QHBoxLayout()
        confidence.setSpacing(12)
        confidence.addWidget(label(t("Assessment confidence")))
        self.confidence_value = label("", "h3")
        confidence.addWidget(self.confidence_value)
        self.confidence_bar = _progress(0)
        confidence.addWidget(self.confidence_bar, 1)
        self.learning = label("", "muted")
        confidence.addWidget(self.learning)
        self.main_card.add(confidence)
        chart_heading = QHBoxLayout()
        chart_heading.addWidget(label(t("Propagation history"), "h3"))
        chart_heading.addStretch(1)
        self.range_buttons: dict[int, QToolButton] = {}
        for hours, caption in HISTORY_RANGES:
            range_button = QToolButton()
            range_button.setProperty("variant", "secondary")
            range_button.setCheckable(True)
            range_button.setText(caption)
            range_button.setToolTip(t("Visible history range"))
            range_button.clicked.connect(lambda _checked=False, value=hours: self.set_range(value))
            chart_heading.addWidget(range_button)
            self.range_buttons[hours] = range_button
        self.history_summary = label("", "small")
        chart_heading.addWidget(self.history_summary)
        self.main_card.add(chart_heading)
        self.chart = IndexHistoryChart(min_height=230)
        self.chart.empty_text = t("Hourly history will appear after the first completed assessment periods.")
        self.chart.value_label = t("Propagation index")
        self.chart.provisional_label = t("Current hour (provisional)")
        self.main_card.add(self.chart)
        outer.addWidget(self.main_card)

        lower = QHBoxLayout()
        lower.setSpacing(14)
        self.model_card = Card(t("Model data"))
        self.model_badge = Badge("", "neutral")
        self.model_card.add_action(self.model_badge)
        self.model_body = QVBoxLayout()
        self.model_card.add(self.model_body)
        self.model_card.add(QWidget(), 1)
        lower.addWidget(self.model_card, 2)
        self.service_card = Card(t("Service data"), t("Technical values currently used by the propagation model."))
        self.service_body = QHBoxLayout()
        self.service_body.setSpacing(24)
        self.service_card.add(self.service_body)
        lower.addWidget(self.service_card, 3)
        outer.addLayout(lower)

    def set_range(self, hours: int) -> None:
        self.history_hours = hours
        self.chart.set_visible_hours(hours)
        for value, range_button in self.range_buttons.items():
            range_button.setChecked(value == hours)

    def update_data(self, item: dict[str, Any], history: dict[str, Any] | None, labels: list[str], end_utc: str) -> None:
        self.band_label.setText(str(item.get("band_label") or "").upper())
        self.name_label.setText(str(item.get("interface_name") or ""))
        index = item.get("condition_index")
        self.status.set_badge(t(item.get("label") or ""), "neutral" if index is None else ("error" if int(index) <= 1 else "ok"))
        clear_layout(self.index_host)
        self.index_host.addWidget(IndexPanel(item))
        self.confidence_value.setText(f"{int(item.get('confidence_percent') or 0)}%")
        self.confidence_bar.setValue(int(item.get("confidence_percent") or 0))
        self.learning.setText(f"{t('Learning history')}: {float(item.get('history_days') or 0):.1f} {t('days')}")

        indexes = list((history or {}).get("indexes") or [None] * len(labels))
        confidence = list((history or {}).get("confidence") or [None] * len(labels))
        chart_labels = list(labels)
        provisional: list[int | None] = [None] * len(chart_labels)
        current_hour = str(item.get("hour_start_utc") or "")
        if current_hour and current_hour == end_utc and index is not None and 0 <= int(index) <= 5:
            if current_hour in chart_labels:
                provisional[chart_labels.index(current_hour)] = int(index)
            else:
                chart_labels.append(current_hour)
                indexes.append(None)
                confidence.append(None)
                provisional.append(int(index))
        results = sum(1 for value in indexes if value is not None)
        recorded = sum(1 for value in confidence if value is not None)
        self.history_summary.setText(f"{t('results')}: {results} · {t('recorded hours')}: {recorded}" if results or any(
            value is not None for value in provisional) else "")
        self.chart.set_data(chart_labels, indexes, provisional)
        if self.history_hours is None:
            first = next((position for position, value in enumerate(indexes) if value is not None or provisional[position] is not None), -1)
            required = len(chart_labels) - first if first >= 0 else HISTORY_RANGES[0][0]
            default = next((hours for hours, _caption in HISTORY_RANGES if hours >= required), HISTORY_RANGES[-1][0])
            self.set_range(default)
        else:
            self.set_range(self.history_hours)

        clear_layout(self.model_body)
        self.model_badge.set_badge(t(item.get("model_stage_label") or ""), "ok" if item.get("model_ready") else "neutral")
        self.model_body.addWidget(label(t(item.get("model_stage_summary") or ""), None, wrap=True))
        self.model_body.addWidget(_stats_grid([
            (t("Hourly periods recorded"), item.get("history_hours", 0)),
            (t("Hours with fixed stations"), item.get("data_hours", 0)),
            (t("Learned fixed stations"), item.get("learned_station_count", 0)),
            (t("Stations with known position"), item.get("learned_positioned_station_count", 0)),
        ]))
        first_text = t("Available") if item.get("model_ready") else f"{item.get('hours_to_first_assessment', 0)} {t('hours remaining')}"
        mature_text = t("Complete") if int(item.get("days_to_mature") or 0) == 0 else f"{item.get('days_to_mature')} {t('days remaining')}"
        for caption, value_text, percent in (
            (t("First assessment"), first_text, item.get("first_assessment_percent")),
            (t("30-day baseline"), mature_text, item.get("maturity_percent")),
        ):
            row = QHBoxLayout()
            row.addWidget(label(caption))
            row.addStretch(1)
            row.addWidget(label(value_text, "h3"))
            self.model_body.addLayout(row)
            self.model_body.addWidget(_progress(percent))

        clear_layout(self.service_body)
        service = item.get("service_data") or {}
        baseline = QVBoxLayout()
        baseline.addWidget(label(t("Reference baseline"), "h3"))
        baseline.addWidget(_stats_grid([
            (t("Baseline hourly periods"), service.get("baseline_hour_count") or 0),
            (t("Usual fixed stations"), f"{float(service.get('normal_station_count') or 0):.1f}"),
            (t("Distance baseline stations"),
             f"{service.get('baseline_distance_clean_station_count') or 0} / {service.get('baseline_distance_station_count') or 0}"),
            (t("Baseline median distance"), distance_km(service.get("baseline_distance_median_km"))),
            (t("Baseline P90 reach"), distance_km(service.get("normal_p90_distance_km"))),
            (t("Baseline distance MAD"), distance_km(service.get("baseline_distance_mad_km"))),
            (t("Far station threshold"), distance_km(service.get("far_threshold_km"))),
            (t("Very far station threshold"), distance_km(service.get("very_far_threshold_km"))),
        ]))
        baseline.addStretch(1)
        sample = QVBoxLayout()
        sample.addWidget(label(t("Assessment sample"), "h3"))
        sample_hour = str(service.get("hour_start_utc") or "")[:16].replace("T", " ")
        sample.addWidget(_stats_grid([
            (t("Sample hour (UTC)"), sample_hour or "—"),
            (t("Fixed stations in sample"), service.get("fixed_station_count") or 0),
            (t("Stations with position in sample"), service.get("positioned_station_count") or 0),
            (t("Sample median distance"), distance_km(service.get("median_distance_km"))),
            (t("Sample P90 reach"), distance_km(service.get("p90_distance_km"))),
            (t("Maximum confirmed distance"), distance_km(service.get("max_confirmed_distance_km"))),
            (t("Far stations confirmed / total"),
             f"{service.get('confirmed_far_station_count') or 0} / {service.get('far_station_count') or 0}"),
            (t("Very far stations confirmed / total"),
             f"{service.get('confirmed_very_far_station_count') or 0} / {service.get('very_far_station_count') or 0}"),
            (t("New geographic areas"), service.get("new_area_count") or 0),
            (t("Observed time segments"), service.get("current_segment_count") or 0),
            (t("RF frames in sample hour"), service.get("rx_total") or 0),
        ]))
        sample.addStretch(1)
        self.service_body.addLayout(baseline, 1)
        self.service_body.addLayout(sample, 1)


class BandConditionPage(Page):
    page_key = "band-condition"

    def build(self) -> None:
        self.panels: dict[int, BandInterfacePanel] = {}
        self.empty_card = Card(t("Band Condition"), t("No interface is currently enabled for band condition assessment."))
        self.empty_card.add_action(button(t("Configure interfaces"), variant="secondary", icon_name="radio-handheld",
                                          on_click=lambda: self.navigate("modems")))
        self.empty_card.hide()
        self.body.addWidget(self.empty_card)
        self.panel_host = QVBoxLayout()
        self.panel_host.setSpacing(22)
        self.body.addLayout(self.panel_host)
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 60000

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.band_condition import get_band_condition_history, get_band_condition_page_data

        with connection_scope():
            return {"page": get_band_condition_page_data(), "history": get_band_condition_history(days=365)}

    def _apply(self, data: dict[str, Any]) -> None:
        interfaces = list((data.get("page") or {}).get("interfaces") or [])
        history = data.get("history") or {}
        labels = [str(value) for value in history.get("labels") or []]
        history_by_id = {int(item.get("interface_id")): item for item in history.get("items") or []}
        self.empty_card.setVisible(not interfaces)
        wanted = [int(item.get("interface_id") or item.get("id") or 0) for item in interfaces]
        if list(self.panels) != wanted:
            clear_layout(self.panel_host)
            self.panels = {}
            for interface_id in wanted:
                panel = BandInterfacePanel(self, interface_id)
                self.panels[interface_id] = panel
                self.panel_host.addWidget(panel)
        for item, interface_id in zip(interfaces, wanted):
            self.panels[interface_id].update_data(item, history_by_id.get(interface_id), labels, str(history.get("end_utc") or ""))
