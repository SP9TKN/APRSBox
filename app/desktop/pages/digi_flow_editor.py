"""Packet Routing flow editor (web: templates/digi_flow_form.html).

The step list follows the web editor's rules exactly: the first and last steps
are the source and target endpoints, APRS-IS flows get their mandatory system
rules, RF TX targets keep the duplicate / rate / path rules in their fixed
places and the palette only offers what the current route allows.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon, symbol_icon_for, symbol_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors, mix
from app.desktop.widgets.common import (
    Badge,
    Card,
    Page,
    button,
    clear_layout,
    icon_button,
    label,
    line_edit,
    set_combo_options,
)
from app.desktop.widgets.pickers import LocationPickerDialog, SymbolPicker

RF_GUARD = "filter_rf_guard"
MESSAGE_DELIVERY = "filter_aprsis_message_delivery"
RF_TX_GUARD = "filter_rf_tx_guard"
ALLOW_RULES = "filter_allow_rules"
APRSIS_SOURCE = "receiver_aprsis"
LOCAL_TX_SOURCE = "receiver_local_tx"
APRSIS_SYSTEM_STEPS = {RF_GUARD, MESSAGE_DELIVERY, RF_TX_GUARD, ALLOW_RULES}
PACKET_GROUPS = {"position", "object", "item", "message", "status", "weather", "telemetry", "query"}

FLOW_HELP_PAGE = "application/packet_routing_flow"


def default_config(step_type: str, ref_value: str = "") -> dict[str, Any]:
    """Web: defaultConfig()."""
    defaults: dict[str, dict[str, Any]] = {
        "receiver_rf": {"rf_port": ref_value or ""},
        APRSIS_SOURCE: {"aprsis_source": ref_value or ""},
        LOCAL_TX_SOURCE: {"local_tx_source": ref_value or "local_tx"},
        RF_GUARD: {},
        MESSAGE_DELIVERY: {},
        RF_TX_GUARD: {"viscous_delay_sec": 5, "flow_rate_per_minute": 6, "flow_burst": 3, "source_rate_per_minute": 2,
                      "source_burst": 2, "duplicate_window_sec": 30},
        ALLOW_RULES: {"callsigns": [], "radius_km": ""},
        "filter_dupe": {"window_sec": 5},
        "filter_direct_only": {},
        "filter_path": {"mode": "allow", "trace_paths": [], "no_trace_paths": []},
        "filter_strict": {},
        "filter_callsign": {"mode": "allow", "callsigns": []},
        "filter_packet_type": {"mode": "allow", "packet_types": []},
        "filter_icon": {"mode": "allow", "icons": []},
        "filter_distance": {"zones": [{"latitude": "", "longitude": "", "radius_km": ""}]},
        "filter_rate_limit": {"rate_limit_rules_text": "* - 60s"},
        "filter_rate_limit_per_callsign": {"packets_per_minute": 30},
        "tx_rf": {"rf_target": ref_value or "", "rf_path": ""},
        "tx_aprsis": {"aprsis_target": ref_value or ""},
        "action_drop": {"note": ""},
        "action_log": {"log_tag": "", "note": ""},
    }
    return copy.deepcopy(defaults.get(step_type, {}))


def unique_lines(items: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items or []:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def text_to_lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def lines_to_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


def normalize_distance_zones(zones: Any, *, keep_empty: bool = False) -> list[dict[str, str]]:
    if not isinstance(zones, list):
        return []
    result: list[dict[str, str]] = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        latitude = str(zone.get("latitude") if zone.get("latitude") is not None else "").strip()
        longitude = str(zone.get("longitude") if zone.get("longitude") is not None else "").strip()
        radius = str(zone.get("radius_km") if zone.get("radius_km") is not None else "").strip()
        if not keep_empty and not latitude and not longitude and not radius:
            continue
        result.append({"latitude": latitude, "longitude": longitude, "radius_km": radius})
        if len(result) >= 3:
            break
    return result


def ensure_distance_config(step: dict[str, Any]) -> list[dict[str, str]]:
    config = step.get("config") if isinstance(step.get("config"), dict) else {}
    legacy = str(config.get("max_km") if config.get("max_km") is not None else "").strip()
    zones = normalize_distance_zones(config.get("zones"), keep_empty=True)
    if not zones and legacy:
        zones = [{"latitude": "", "longitude": "", "radius_km": legacy}]
    if not zones:
        zones = [{"latitude": "", "longitude": "", "radius_km": ""}]
    step["config"] = {"zones": zones}
    return zones


def normalize_rate_limit_step(step: dict[str, Any]) -> None:
    """Web: initial normalisation of legacy rate-limit configs into rate_limit_rules_text."""
    config = step.get("config") if isinstance(step.get("config"), dict) else {}
    text = config.get("rate_limit_rules_text")
    if text is None or not str(text).strip():
        rules = config.get("rate_limit_rules")
        if isinstance(rules, list) and rules:
            config["rate_limit_rules_text"] = "\n".join(
                f"{str(rule.get('source_callsign_pattern') or rule.get('pattern') or '*').strip() or '*'} - "
                f"{str(rule.get('rate_limit_seconds') or rule.get('seconds') or rule.get('limit') or 60).strip()}s"
                for rule in rules if isinstance(rule, dict)
            )
        elif any(config.get(key) is not None for key in ("source_callsign_pattern", "rate_limit_seconds", "packets_per_minute")):
            seconds = config.get("rate_limit_seconds")
            if seconds is None:
                seconds = config.get("packets_per_minute")
            config["rate_limit_rules_text"] = f"{str(config.get('source_callsign_pattern') or '*').strip() or '*'} - {str(seconds if seconds is not None else 60).strip()}s"
        else:
            config["rate_limit_rules_text"] = "* - 60s"
    for key in ("rate_limit_rules", "source_callsign_pattern", "rate_limit_seconds", "packets_per_minute"):
        config.pop(key, None)
    step["config"] = config


def mode_label(mode: str) -> str:
    return t("allow") if mode == "allow" else (t("deny") if mode == "deny" else mode)


def packet_type_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in PACKET_GROUPS:
        return text.lower()
    return {
        "M": t("legacy M (mobile position)"),
        "S": t("legacy S (stationary position)"),
        "O": t("legacy O (object)"),
        "W": t("legacy W (weather-only)"),
    }.get(text.upper(), text)


def step_summary(step: dict[str, Any]) -> str:
    """Web: stepSummary()."""
    config = step.get("config") or {}
    step_type = step.get("step_type")
    if step_type == "receiver_rf":
        return f"{t('RF port')}: {config.get('rf_port') or '-'}"
    if step_type == APRSIS_SOURCE:
        return f"{t('APRS-IS source')}: {config.get('aprsis_source') or '-'}"
    if step_type == LOCAL_TX_SOURCE:
        return t("Locally generated APRSBox TX frames")
    if step_type == RF_GUARD:
        return t("APRS/q validation · loop prevention · initial duplicate check")
    if step_type == MESSAGE_DELIVERY:
        return t("All active TX-enabled TNC interfaces · direct RF · exact SSID · 60 min")
    if step_type == RF_TX_GUARD:
        return tf("Final duplicate check · delay {seconds}s · rate limits · third-party", seconds=config.get("viscous_delay_sec") or 5)
    if step_type == ALLOW_RULES:
        callsigns = config.get("callsigns") if isinstance(config.get("callsigns"), list) else []
        if not callsigns and not str(config.get("radius_km") or "").strip():
            return t("Message-only mode (no ordinary station traffic)")
        return f"{t('callsigns')}: {len(callsigns)} · {t('Radius (km)')}: {config.get('radius_km') or '-'} · AND · {t('default deny')}"
    if step_type == "filter_dupe":
        return f"{t('Window')}: {config.get('window_sec') or '-'} {t('sec')}"
    if step_type == "filter_path":
        trace = config.get("trace_paths") or config.get("paths") or []
        no_trace = config.get("no_trace_paths") or []
        return f"{t('Mode')}: {mode_label(config.get('mode') or 'allow')}, {t('paths')}: {len(trace) + len(no_trace)}"
    if step_type == "filter_strict":
        return t("Rejects TCPIP/TCPXX, NOGATE/RFONLY and invalid third-party packets")
    if step_type == "filter_direct_only":
        return t("Passes only direct packets")
    if step_type == "filter_callsign":
        return f"{t('Mode')}: {mode_label(config.get('mode') or 'allow')}, {t('callsigns')}: {len(config.get('callsigns') or [])}"
    if step_type == "filter_packet_type":
        labels = [packet_type_label(value) for value in config.get("packet_types") or []]
        return f"{t('Mode')}: {mode_label(config.get('mode') or 'allow')}, {t('packet groups')}: {', '.join(item for item in labels if item) or t('none')}"
    if step_type == "filter_icon":
        return f"{t('Mode')}: {mode_label(config.get('mode') or 'allow')}, {t('icons')}: {', '.join(config.get('icons') or []) or t('none')}"
    if step_type == "filter_distance":
        return f"{t('Distance zones')}: {len(normalize_distance_zones(config.get('zones')))}"
    if step_type == "filter_rate_limit":
        rules = str(config.get("rate_limit_rules_text") or "")
        count = len([line for line in rules.splitlines() if line.strip() and not line.strip().startswith("#")])
        return f"{t('Rules')}: {count or '-'}"
    if step_type == "filter_rate_limit_per_callsign":
        return f"{t('Per callsign')}: {config.get('packets_per_minute') or '-'} {t('pkt/min')}"
    if step_type == "tx_rf":
        return f"{t('RF target')}: {config.get('rf_target') or '-'} · path: {config.get('rf_path') or 'direct'}"
    if step_type == "tx_aprsis":
        return f"{t('APRS-IS target')}: {config.get('aprsis_target') or '-'}"
    if step_type == "action_drop":
        return str(config.get("note") or t("Drop packet"))
    if step_type == "action_log":
        parts = []
        if config.get("log_tag"):
            parts.append(f"{t('Tag')}: {config.get('log_tag')}")
        if config.get("note"):
            parts.append(str(config.get("note")))
        return " | ".join(parts) or t("Log packet")
    return ""


def chip(text: str, color: QColor, *, bold: bool = True) -> QLabel:
    """Soft coloured pill (web: color-mix based badges)."""
    colors = current_colors()
    widget = QLabel(text)
    foreground = mix(color, colors.color("text"), 0.75)
    border = mix(color, colors.color("border"), 0.48)
    background = mix(color, colors.color("panel-alt"), 0.15)
    widget.setStyleSheet(
        f"QLabel {{ color: {foreground.name()}; background: {background.name()}; border: 1px solid {border.name()};"
        f" border-radius: 9px; padding: 1px 8px; font-size: 8pt; {'font-weight: 700;' if bold else ''} }}"
    )
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return widget


def scope_color(tone: str) -> QColor:
    colors = current_colors()
    if tone == "aprsis-to-rf":
        return QColor("#b99cff")
    if tone == "aprsis-target":
        return colors.color("success")
    return colors.color("warning")


def badge_color(kind: str) -> QColor:
    colors = current_colors()
    return colors.color("warning") if kind == "rule" else colors.color("accent")


def category_color(category: str) -> QColor:
    colors = current_colors()
    return {"source": colors.color("accent"), "filter": colors.color("warning"), "target": colors.color("success")}.get(
        category, colors.color("border"))


class PaletteItem(QFrame):
    """One addable filter/rule (web: .digi-palette-button)."""

    clicked = Signal(str)

    def __init__(self, step_type: str, meta: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_type = step_type
        self.setObjectName("PaletteItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        colors = current_colors()
        self.setStyleSheet(
            f"QFrame#PaletteItem {{ background: {colors.css('panel-alt')}; border: 1px solid {colors.css('border')}; border-radius: 8px; }}"
            f"QFrame#PaletteItem:hover {{ border: 1px solid {colors.css('accent')}; }}"
            f"QFrame#PaletteItem[required=\"true\"] {{ border: 1px solid {colors.css('warning')}; }}"
            f"QFrame#PaletteItem:disabled {{ background: {colors.css('panel')}; border: 1px dashed {colors.css('border')}; }}"
            f"QFrame#PaletteItem:disabled QLabel {{ color: {colors.css('muted')}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(chip(str(meta.get("badge") or ""), badge_color(str(meta.get("palette_kind") or "filter"))))
        if meta.get("scope_label"):
            chips.addWidget(chip(str(meta.get("scope_label")), scope_color(str(meta.get("scope_tone") or "")), bold=False))
        chips.addStretch(1)
        layout.addLayout(chips)
        title = label(str(meta.get("label") or step_type), None, wrap=True)
        title.setStyleSheet("font-weight: 700;")
        layout.addWidget(title)
        layout.addWidget(label(str(meta.get("description") or ""), "small", wrap=True))
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_required(self, required: bool) -> None:
        if self.property("required") != required:
            self.setProperty("required", required)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.step_type)
        super().mouseReleaseEvent(event)

    def changeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ForbiddenCursor)


class DigiFlowEditorPage(Page):
    page_key = "digi-flow-edit"

    def build(self) -> None:
        from app.services.digi_flows import FILTER_STEP_TYPES, LEGACY_DEFAULT_STEP_TITLES, get_digi_flow_type_meta

        self.type_meta: dict[str, dict[str, Any]] = get_digi_flow_type_meta()
        self.filter_types = [step_type for step_type in FILTER_STEP_TYPES]
        self.legacy_titles = {key: set(value) for key, value in LEGACY_DEFAULT_STEP_TITLES.items()}
        self.flow_id: int | None = None
        self.steps: list[dict[str, Any]] = []
        self.expanded: int = -1
        self.endpoints: dict[str, Any] = {"source": [], "target": [], "target_by_source_kind": {}}
        self.source_kind = "receiver_rf"
        self.source_ref = ""
        self.target_kind = "action_log"
        self.target_ref = "log-only"
        self.executions_frozen = False
        self._execution_signature = ""
        self._pending_argument: Any = None

        self.header_card = Card(t("New Packet Routing Flow"))
        self.header_card.add_action(button(t("Back"), variant="secondary", icon_name="arrow-left", on_click=lambda: self.navigate("digi-flows")))
        self.header_card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, FLOW_HELP_PAGE)))
        self.header_card.add(self.flash)
        self.body.addWidget(self.header_card)

        form_card = Card()
        top = QGridLayout()
        top.setHorizontalSpacing(14)
        top.setVerticalSpacing(6)
        top.addWidget(label(t("Name")), 0, 0)
        top.addWidget(label(t("Description")), 0, 1)
        self.name = line_edit("")
        self.description = line_edit("")
        top.addWidget(self.name, 1, 0)
        top.addWidget(self.description, 1, 1)
        self.enabled = QCheckBox(t("Flow is enabled"))
        top.addWidget(self.enabled, 1, 2)
        top.addWidget(label(t("Source"), "h3"), 2, 0)
        top.addWidget(label(t("Target"), "h3"), 2, 1)
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        self.source_combo.currentIndexChanged.connect(lambda _index: self._source_changed())
        self.target_combo.currentIndexChanged.connect(lambda _index: self._target_changed())
        top.addWidget(self.source_combo, 3, 0)
        top.addWidget(self.target_combo, 3, 1)
        top.setColumnStretch(0, 1)
        top.setColumnStretch(1, 1)
        form_card.add(top)

        builder = QHBoxLayout()
        builder.setSpacing(14)
        palette_box = QVBoxLayout()
        palette_box.addWidget(label(t("Available filters and rules"), "h2"))
        palette_scroll = QScrollArea()
        palette_scroll.setWidgetResizable(True)
        palette_scroll.setFrameShape(QFrame.Shape.NoFrame)
        palette_host = QWidget()
        palette_host.setObjectName("PageBody")
        palette_layout = QVBoxLayout(palette_host)
        palette_layout.setContentsMargins(0, 0, 6, 0)
        palette_layout.setSpacing(8)
        self.palette_items: dict[str, PaletteItem] = {}
        for step_type in self.filter_types:
            if step_type == "filter_rate_limit_per_callsign":
                continue
            item = PaletteItem(step_type, self.type_meta.get(step_type, {}))
            item.clicked.connect(self._add_step)
            palette_layout.addWidget(item)
            self.palette_items[step_type] = item
        palette_layout.addStretch(1)
        palette_scroll.setWidget(palette_host)
        palette_scroll.setMinimumWidth(300)
        palette_scroll.setMaximumWidth(380)
        palette_scroll.setMinimumHeight(520)
        palette_box.addWidget(palette_scroll, 1)
        builder.addLayout(palette_box, 2)

        steps_box = QVBoxLayout()
        steps_box.addWidget(label(t("Current flow steps"), "h2"))
        self.chain_host = QWidget()
        self.chain = QVBoxLayout(self.chain_host)
        self.chain.setContentsMargins(0, 0, 0, 0)
        self.chain.setSpacing(4)
        steps_box.addWidget(self.chain_host)
        steps_box.addStretch(1)
        actions = QHBoxLayout()
        self.save_button = button(t("Create Flow"), icon_name="content-save-outline", on_click=self._save)
        actions.addWidget(self.save_button)
        self.duplicate_button = button(t("Duplicate into draft"), variant="secondary", icon_name="content-copy",
                                       on_click=lambda: self.navigate("digi-flow-edit", {"duplicate": self.flow_id}))
        actions.addWidget(self.duplicate_button)
        actions.addStretch(1)
        steps_box.addLayout(actions)
        builder.addLayout(steps_box, 5)
        form_card.add(builder)
        self.body.addWidget(form_card)

        self.executions_card = Card(t("Routing Queue Log"))
        self.freeze_button = button(t("Freeze"), variant="secondary", icon_name="pause", on_click=self._toggle_freeze)
        self.executions_card.add_action(self.freeze_button)
        self.executions_host = QVBoxLayout()
        self.executions_host.setSpacing(10)
        self.executions_card.add(self.executions_host)
        self.executions_empty = label(t("No packet executions recorded for this flow yet."), "muted")
        self.executions_card.add(self.executions_empty)
        self.body.addWidget(self.executions_card)
        self.body.addStretch(1)

        self.execution_loader = SerialLoader(self._load_executions, self._apply_executions, self)
        self.auto_refresh_ms = 3000

    # ================================================================ loading
    def open_argument(self, argument: Any) -> None:
        from app.services.digi_flows import build_digi_flow_editor_payload, get_digi_flow

        self.flash.hide()
        flow_id: int | None = None
        form: dict[str, Any]
        message: str | None = None
        if isinstance(argument, dict) and argument.get("duplicate"):
            source = get_digi_flow(int(argument["duplicate"]))
            if source is None:
                self.flash.error(t("DIGI Flow not found"))
                form = build_digi_flow_editor_payload()
            else:
                form = build_digi_flow_editor_payload(source)
                form["name"] = f"{form['name']} copy"
                message = ("Flow duplicated into a new draft. You can keep the same source and target; "
                           "only one flow per source+target pair can stay enabled at a time.")
        elif argument not in (None, "", {}):
            try:
                flow_id = int(argument if not isinstance(argument, dict) else argument.get("id"))
            except (TypeError, ValueError):
                flow_id = None
            flow = get_digi_flow(flow_id) if flow_id is not None else None
            if flow is None:
                self.flash.error(t("DIGI Flow not found"))
                flow_id = None
                form = build_digi_flow_editor_payload()
            else:
                form = build_digi_flow_editor_payload(flow)
            if isinstance(argument, dict) and argument.get("flash"):
                message = str(argument["flash"])
        else:
            form = build_digi_flow_editor_payload()
        self._load_form(form, flow_id)
        if message:
            self.flash.success(t(message))

    def _load_form(self, form: dict[str, Any], flow_id: int | None) -> None:
        from app.services.digi_flows import get_digi_flow_endpoint_options

        self.flow_id = flow_id
        self.header_card.set_title(t("Edit Packet Routing Flow") if flow_id else t("New Packet Routing Flow"))
        self.save_button.setText(t("Save Changes") if flow_id else t("Create Flow"))
        self.duplicate_button.setVisible(flow_id is not None)
        self.executions_card.setVisible(flow_id is not None)
        self._execution_signature = ""
        clear_layout(self.executions_host)
        self.name.setText(str(form.get("name") or ""))
        self.description.setText(str(form.get("description") or ""))
        self.enabled.setChecked(bool(form.get("enabled")))
        self.endpoints = get_digi_flow_endpoint_options(
            selected_source_selector=str(form.get("source_selector") or "").strip() or None,
            selected_target_selector=str(form.get("target_selector") or "").strip() or None,
            current_flow_id=flow_id,
        )
        self.source_kind = str(form.get("source_kind") or "receiver_rf")
        self.source_ref = str(form.get("source_ref") or "")
        self.target_kind = str(form.get("target_kind") or "action_log")
        self.target_ref = str(form.get("target_ref") or "log-only")
        steps = [dict(step) for step in form.get("steps") or []]
        normalized: list[dict[str, Any]] = []
        for step in steps:
            step = {**step, "config": dict(step.get("config") or {})}
            step["title"] = self._normalized_title(step)
            if step.get("step_type") == "filter_distance":
                ensure_distance_config(step)
            if step.get("step_type") == "filter_rate_limit":
                normalize_rate_limit_step(step)
            normalized.append(step)
        if len(normalized) < 2:
            normalized = [
                {"step_type": "receiver_rf", "title": self._label("receiver_rf"), "enabled": 1, "config": default_config("receiver_rf")},
                {"step_type": "action_log", "title": self._label("action_log"), "enabled": 1, "config": default_config("action_log", "log-only")},
            ]
        self.steps = normalized
        self.expanded = -1
        self._fill_endpoint_combos(str(form.get("source_selector") or ""), str(form.get("target_selector") or ""))
        self.render()

    # ============================================================== endpoints
    def _target_options(self, source_kind: str) -> list[dict[str, Any]]:
        mapped = (self.endpoints.get("target_by_source_kind") or {}).get(str(source_kind or "").strip())
        return list(mapped) if mapped else list(self.endpoints.get("target") or [])

    def _fill_endpoint_combos(self, source_selector: str, target_selector: str) -> None:
        sources = list(self.endpoints.get("source") or [])
        self.source_combo.blockSignals(True)
        if sources:
            set_combo_options(self.source_combo, [(option["value"], option["label"]) for option in sources], None, translate=False)
            index = self.source_combo.findData(source_selector)
            self.source_combo.setCurrentIndex(index if index >= 0 else 0)
        else:
            set_combo_options(self.source_combo, [("", t("Not configured"))], "", translate=False)
        self.source_combo.blockSignals(False)
        self._fill_target_combo(target_selector)

    def _fill_target_combo(self, target_selector: str) -> None:
        source = self._selected_source()
        targets = self._target_options(source.get("kind") if source else "")
        self.target_combo.blockSignals(True)
        if targets:
            set_combo_options(self.target_combo, [(option["value"], option["label"]) for option in targets], None, translate=False)
            index = self.target_combo.findData(target_selector)
            self.target_combo.setCurrentIndex(index if index >= 0 else 0)
        else:
            set_combo_options(self.target_combo, [("", t("Not configured"))], "", translate=False)
        self.target_combo.blockSignals(False)

    def _selected_source(self) -> dict[str, Any] | None:
        value = self.source_combo.currentData()
        return next((option for option in self.endpoints.get("source") or [] if option.get("value") == value), None)

    def _selected_target(self) -> dict[str, Any] | None:
        source = self._selected_source()
        value = self.target_combo.currentData()
        return next((option for option in self._target_options(source.get("kind") if source else "") if option.get("value") == value), None)

    def _source_changed(self) -> None:
        self._fill_target_combo(str(self.target_combo.currentData() or ""))
        self.render()

    def _target_changed(self) -> None:
        self.render()

    def _sync_boundary_steps(self) -> None:
        """Web: syncBoundarySteps()."""
        source = self._selected_source()
        target = self._selected_target()
        if not source or not target or len(self.steps) < 2:
            return
        self.source_kind, self.source_ref = str(source["kind"]), str(source["ref"])
        self.target_kind, self.target_ref = str(target["kind"]), str(target["ref"])
        source_step, target_step = self.steps[0], self.steps[-1]
        source_step["step_type"] = self.source_kind
        source_step["title"] = self._label(self.source_kind)
        if not isinstance(source_step.get("config"), dict) or self._category(source_step["step_type"]) != "source":
            source_step["config"] = default_config(self.source_kind, self.source_ref)
        if self.source_kind == "receiver_rf":
            source_step["config"]["rf_port"] = self.source_ref
        elif self.source_kind == APRSIS_SOURCE:
            source_step["config"]["aprsis_source"] = self.source_ref
        elif self.source_kind == LOCAL_TX_SOURCE:
            source_step["config"]["local_tx_source"] = self.source_ref or "local_tx"
        target_step["step_type"] = self.target_kind
        target_step["title"] = self._label(self.target_kind)
        if not isinstance(target_step.get("config"), dict) or self._category(target_step["step_type"]) != "target":
            target_step["config"] = default_config(self.target_kind, self.target_ref)
        if self.target_kind == "tx_rf":
            target_step["config"]["rf_target"] = self.target_ref
            target_step["config"].setdefault("rf_path", "")
            if target_step["config"].get("rf_path") is None:
                target_step["config"]["rf_path"] = ""
        elif self.target_kind == "tx_aprsis":
            target_step["config"]["aprsis_target"] = self.target_ref
        elif self.target_kind == "action_log":
            target_step["config"]["log_tag"] = self.target_ref
        elif self.target_kind == "action_drop":
            target_step["config"]["note"] = self.target_ref

    # ================================================================== rules
    def _label(self, step_type: str) -> str:
        return str((self.type_meta.get(step_type) or {}).get("label") or step_type)

    def _category(self, step_type: str) -> str:
        return str((self.type_meta.get(step_type) or {}).get("category") or "")

    def _normalized_title(self, step: dict[str, Any]) -> str:
        from app.services.digi_flows import STEP_TYPE_META

        step_type = str(step.get("step_type") or "")
        title = str(step.get("title") or "").strip()
        current = self._label(step_type)
        english_default = str((STEP_TYPE_META.get(step_type) or {}).get("label") or "")
        # Default titles are stored in English; show them in the window language.
        if not title or title == english_default or title in self.legacy_titles.get(step_type, set()):
            return current
        return title

    def is_tx_rf_target(self) -> bool:
        return self.target_kind == "tx_rf"

    def is_aprsis_source(self) -> bool:
        return self.source_kind == APRSIS_SOURCE

    def is_aprsis_target(self) -> bool:
        return self.target_kind == "tx_aprsis"

    def is_strict_aprsis_to_rf(self) -> bool:
        return self.is_aprsis_source() and self.is_tx_rf_target()

    def is_fixed_tx_rf_step(self, step_type: str) -> bool:
        return self.is_tx_rf_target() and not self.is_aprsis_source() and step_type in {"filter_dupe", "filter_rate_limit", "filter_path"}

    def _middle(self) -> list[dict[str, Any]]:
        return self.steps[1:-1]

    def _has_middle(self, step_type: str) -> bool:
        return any(step.get("step_type") == step_type for step in self._middle())

    def _enforce_aprsis_target(self) -> None:
        """Web: enforceAprsisFlowRestrictions()."""
        if len(self.steps) < 2:
            return
        source_step, target_step = self.steps[0], self.steps[-1]
        if not self.is_aprsis_target():
            self.steps = [source_step, *[step for step in self._middle() if step.get("step_type") != "filter_strict"], target_step]
            return
        existing = next((step for step in self._middle() if step.get("step_type") == "filter_strict"), None)
        strict = dict(existing) if existing else {}
        strict.update({"step_type": "filter_strict", "title": self._label("filter_strict"), "enabled": 1, "config": {}})
        self.steps = [source_step, strict, target_step]

    def _enforce_aprsis_source(self) -> None:
        """Web: enforceAprsisSourceRestrictions()."""
        if len(self.steps) < 2:
            return
        source_step, target_step = self.steps[0], self.steps[-1]
        middle = self._middle()
        if not self.is_aprsis_source():
            self.steps = [source_step, *[step for step in middle if step.get("step_type") not in APRSIS_SYSTEM_STEPS], target_step]
            return
        existing_guard = next((step for step in middle if step.get("step_type") == RF_GUARD), None)
        existing_delivery = next((step for step in middle if step.get("step_type") == MESSAGE_DELIVERY), None)
        existing_tx_guard = next((step for step in middle if step.get("step_type") == RF_TX_GUARD), None)
        existing_allow = next((step for step in middle if step.get("step_type") == ALLOW_RULES), None)
        legacy_guard_config = dict((existing_guard or {}).get("config") or {})
        guard = {**existing_guard, "config": {}} if existing_guard else {
            "step_type": RF_GUARD, "title": self._label(RF_GUARD), "enabled": 1, "config": default_config(RF_GUARD)}
        if existing_tx_guard:
            tx_guard = {**existing_tx_guard, "config": {**default_config(RF_TX_GUARD), **dict(existing_tx_guard.get("config") or {})}}
        else:
            tx_guard = {"step_type": RF_TX_GUARD, "title": self._label(RF_TX_GUARD), "enabled": 1,
                        "config": {**default_config(RF_TX_GUARD), **legacy_guard_config}}
        delivery = {**existing_delivery, "config": {}} if existing_delivery else {
            "step_type": MESSAGE_DELIVERY, "title": self._label(MESSAGE_DELIVERY), "enabled": 1, "config": default_config(MESSAGE_DELIVERY)}
        if existing_allow:
            allow_config = dict(existing_allow.get("config") or {})
            callsigns = allow_config.get("callsigns")
            allow = {**existing_allow, "config": {
                "callsigns": unique_lines(str(value or "").strip().upper() for value in callsigns) if isinstance(callsigns, list) else [],
                "radius_km": str(allow_config.get("radius_km") or "").strip(),
            }}
        else:
            allow = {"step_type": ALLOW_RULES, "title": self._label(ALLOW_RULES), "enabled": 1, "config": default_config(ALLOW_RULES)}
        for step, step_type in ((guard, RF_GUARD), (delivery, MESSAGE_DELIVERY), (tx_guard, RF_TX_GUARD), (allow, ALLOW_RULES)):
            step["title"] = self._label(step_type)
            step["enabled"] = 1
        guard["config"] = {}
        ordinary = [] if self.is_strict_aprsis_to_rf() else [step for step in middle if step.get("step_type") not in APRSIS_SYSTEM_STEPS]
        tx_guard_steps = [tx_guard] if self.is_tx_rf_target() else []
        delivery_steps = [delivery] if self.is_tx_rf_target() else []
        self.steps = [source_step, guard, *delivery_steps, allow, *ordinary, *tx_guard_steps, target_step]

    def _enforce_tx_rf(self) -> None:
        """Web: enforceTxRfFlowRestrictions()."""
        if len(self.steps) < 2:
            return
        source_step, target_step = self.steps[0], self.steps[-1]
        middle = self._middle()
        if not self.is_tx_rf_target() or self.is_aprsis_source():
            self.steps = [source_step, *[step for step in middle if step.get("step_type") != "filter_rate_limit"], target_step]
            return
        dupe = [step for step in middle if step.get("step_type") == "filter_dupe"]
        other = [step for step in middle if step.get("step_type") not in {"filter_dupe", "filter_rate_limit", "filter_path"}]
        rate = [step for step in middle if step.get("step_type") == "filter_rate_limit"]
        path = [step for step in middle if step.get("step_type") == "filter_path"]
        self.steps = [source_step, *dupe, *other, *rate, *path, target_step]

    def _apply_rules(self) -> None:
        expanded_step = self.steps[self.expanded] if 0 <= self.expanded < len(self.steps) else None
        self._sync_boundary_steps()
        self._enforce_aprsis_target()
        self._enforce_aprsis_source()
        self._enforce_tx_rf()
        self._sync_boundary_steps()
        # Keep the same card open when rules reordered the list.
        if expanded_step is not None:
            self.expanded = next((index for index, step in enumerate(self.steps) if step is expanded_step), -1)

    def _sync_palette(self) -> None:
        """Web: syncPaletteState()."""
        strict_aprsis_to_rf = self.is_strict_aprsis_to_rf()
        messages = {
            RF_GUARD: t("The APRS-IS Input Safety Rule is mandatory, system-managed, and available only for an APRS-IS source."),
            MESSAGE_DELIVERY: t("The APRS-IS Message Delivery Rule is mandatory, system-managed and not configurable. It automatically uses all active TNC interfaces with RF transmission allowed."),
            RF_TX_GUARD: t("The APRS-IS to RF TX Safety Rule is mandatory, system-managed, and fixed immediately before TX RF."),
            ALLOW_RULES: t("The APRS-IS Callsign and Radius Rule is mandatory and system-managed; an empty configuration denies traffic not authorized by the Message Delivery Rule."),
        }
        for step_type, item in self.palette_items.items():
            tooltip = ""
            visible = not (strict_aprsis_to_rf and step_type not in APRSIS_SYSTEM_STEPS)
            item.setVisible(visible)
            enabled = True
            item.set_required(False)
            if not visible:
                enabled = False
            elif step_type in APRSIS_SYSTEM_STEPS:
                enabled = False
                tooltip = messages[step_type]
                item.set_required(self.is_aprsis_source() and (step_type != RF_TX_GUARD or self.is_tx_rf_target()))
            elif self.is_aprsis_target():
                enabled = False
                tooltip = t("An APRS-IS target flow is system-restricted to one mandatory uplink safety rule.")
            elif step_type == "filter_rate_limit" and not self.is_tx_rf_target():
                enabled = False
            elif step_type == "filter_strict":
                enabled = False
                tooltip = t("The APRS-IS Uplink Safety Rule is mandatory, system-managed, and available only for APRS-IS target flows.")
            elif step_type == "filter_dupe" and self._has_middle("filter_dupe"):
                enabled = False
                tooltip = t("Duplicate filter (viscous-delay) is already used in this flow.")
            elif step_type == "filter_rate_limit" and self._has_middle("filter_rate_limit"):
                enabled = False
                tooltip = t("Transmission Rate Filter is already used in this flow.")
            elif step_type == "filter_distance" and self._has_middle("filter_distance"):
                enabled = False
                tooltip = t("Distance filter is already used in this flow.")
            item.setEnabled(enabled)
            item.setToolTip(tooltip or str((self.type_meta.get(step_type) or {}).get("description") or ""))

    def _add_step(self, step_type: str) -> None:
        """Web: palette button click handler."""
        if step_type not in self.filter_types or self.is_aprsis_target() or step_type == "filter_strict":
            return
        if self.is_strict_aprsis_to_rf() or step_type in APRSIS_SYSTEM_STEPS:
            return
        if step_type == "filter_rate_limit" and not self.is_tx_rf_target():
            return
        if step_type in {"filter_dupe", "filter_rate_limit", "filter_distance"} and self._has_middle(step_type):
            return
        insert_index = len(self.steps) - 1
        if step_type == "filter_dupe":
            insert_index = 1
        elif step_type == "filter_rate_limit":
            path_index = next((index for index, step in enumerate(self.steps)
                               if 0 < index < len(self.steps) - 1 and step.get("step_type") == "filter_path"), -1)
            insert_index = path_index if path_index > 0 else len(self.steps) - 1
        self.steps.insert(insert_index, {"step_type": step_type, "title": self._label(step_type), "enabled": 1,
                                         "config": default_config(step_type)})
        self.expanded = insert_index
        self.render()

    # ================================================================= render
    def render(self) -> None:
        self._apply_rules()
        self._sync_palette()
        clear_layout(self.chain)
        for index, step in enumerate(self.steps):
            if step.get("step_type") == "filter_distance":
                ensure_distance_config(step)
            self.chain.addWidget(self._step_card(index, step))
            if index < len(self.steps) - 1:
                warning = (len(self.steps) == 2 and index == 0 and self.steps[-1].get("step_type") == "tx_rf"
                           and not self.is_aprsis_source())
                if warning:
                    connector = label("✕  " + t("The RF Digipeating Path Rule is required before an RF TX target."), "error")
                else:
                    connector = label("↓", "muted")
                connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.chain.addWidget(connector)

    def _step_card(self, index: int, step: dict[str, Any]) -> QWidget:
        colors = current_colors()
        step_type = str(step.get("step_type") or "")
        category = self._category(step_type)
        card = QFrame()
        card.setObjectName("StepCard")
        accent = category_color(category)
        card.setStyleSheet(
            f"QFrame#StepCard {{ background: {colors.css('panel-alt')}; border: 1px solid {colors.css('border')};"
            f" border-left: 4px solid {accent.name()}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        order = label(str(index + 1), "h3")
        order.setFixedWidth(22)
        order.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(order)
        meta = self.type_meta.get(step_type) or {}
        header.addWidget(chip(str(meta.get("badge") or ""), badge_color(str(meta.get("palette_kind") or "filter")) if category == "filter" else accent))
        mode = str((step.get("config") or {}).get("mode") or "").strip().lower()
        if mode in {"allow", "deny"}:
            header.addWidget(chip(mode_label(mode).upper(), colors.color("success") if mode == "allow" else colors.color("danger")))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = label(self._normalized_title(step), None)
        title.setStyleSheet("font-weight: 700;")
        titles.addWidget(title)
        summary = label(step_summary(step), "small", wrap=True)
        titles.addWidget(summary)
        header.addLayout(titles, 1)

        aprsis_locked = self.is_aprsis_target()
        system_step = self.is_aprsis_source() and step_type in APRSIS_SYSTEM_STEPS
        strict_system = aprsis_locked and step_type == "filter_strict" and index == 1
        tx_locked = self.is_fixed_tx_rf_step(step_type)
        previous_type = str((self.steps[index - 1] if index > 0 else {}).get("step_type") or "")
        next_type = str((self.steps[index + 1] if index + 1 < len(self.steps) else {}).get("step_type") or "")
        enabled = QCheckBox(t("Enabled"))
        enabled.setChecked(True if (strict_system or system_step) else bool(step.get("enabled")))
        enabled.setEnabled(not (strict_system or system_step))
        enabled.toggled.connect(lambda checked, value=step: self._set_step_enabled(value, checked))
        header.addWidget(enabled)
        is_open = index == self.expanded
        toggle = icon_button("chevron-up" if is_open else "chevron-down", t("Collapse step") if is_open else t("Expand step"),
                             lambda value=index: self._toggle_expanded(value))
        header.addWidget(toggle)
        is_filter = category == "filter"
        up = icon_button("arrow-up", t("Move step up"), lambda value=index: self._move_step(value, -1))
        up.setEnabled(not (index <= 1 or not is_filter or tx_locked or (self.is_tx_rf_target() and previous_type == "filter_dupe")
                           or aprsis_locked or system_step))
        header.addWidget(up)
        down = icon_button("arrow-down", t("Move step down"), lambda value=index: self._move_step(value, 1))
        down.setEnabled(not (index >= len(self.steps) - 2 or not is_filter or tx_locked
                             or (self.is_tx_rf_target() and next_type == "filter_path") or aprsis_locked or system_step))
        header.addWidget(down)
        delete = icon_button("delete-outline", t("Delete step"), lambda value=index: self._delete_step(value),
                             color=colors.color("danger"))
        delete.setEnabled(is_filter and not aprsis_locked and not system_step)
        header.addWidget(delete)
        layout.addLayout(header)
        if is_open:
            layout.addWidget(self._step_editor(step, strict_system=strict_system, system_step=system_step))
        return card

    def _step_editor(self, step: dict[str, Any], *, strict_system: bool, system_step: bool) -> QWidget:
        step_type = str(step.get("step_type") or "")
        meta = self.type_meta.get(step_type) or {}
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(30, 0, 0, 4)
        layout.setSpacing(8)
        if meta.get("help_page"):
            help_row = QHBoxLayout()
            help_row.addWidget(icon_button("help-circle-outline", f"{t('Help')}: {meta.get('label') or ''}",
                                           lambda page=str(meta.get("help_page")): show_help(self, page)))
            help_row.addWidget(label(str(meta.get("description") or ""), "small", wrap=True), 1)
            layout.addLayout(help_row)
        help_lines = list(meta.get("editor_help_lines") or [])
        if help_lines:
            layout.addWidget(label("\n".join(f"• {line}" for line in help_lines), "small", wrap=True))
        if strict_system or system_step:
            locked = {
                RF_GUARD: t("The APRS-IS Input Safety Rule is mandatory, system-managed, and available only for an APRS-IS source."),
                MESSAGE_DELIVERY: t("The APRS-IS Message Delivery Rule is mandatory, system-managed and not configurable. It automatically uses all active TNC interfaces with RF transmission allowed."),
                RF_TX_GUARD: t("The APRS-IS to RF TX Safety Rule is mandatory, system-managed, and fixed immediately before TX RF."),
                ALLOW_RULES: t("The APRS-IS Callsign and Radius Rule is mandatory and system-managed; an empty configuration denies traffic not authorized by the Message Delivery Rule."),
            }.get(step_type, t("An APRS-IS target flow is system-restricted to one mandatory uplink safety rule."))
            layout.addWidget(label(locked, "warning", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        row = 0
        grid.addWidget(label(t("Card Title")), row, 0)
        title_edit = line_edit(self._normalized_title(step))
        title_edit.setEnabled(not (strict_system or system_step))
        title_edit.textEdited.connect(lambda text, value=step: value.__setitem__("title", text))
        grid.addWidget(title_edit, row, 1)
        row += 1
        config = step.setdefault("config", {})
        for field in meta.get("config_fields") or []:
            widget = self._field_widget(step, config, field)
            caption = label(str(field.get("label") or field.get("name")), None, wrap=True)
            caption.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(caption, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, row, 1)
            row += 1
            notes = [str(line) for line in field.get("help_lines") or []]
            if field.get("help_text"):
                notes.append(str(field.get("help_text")))
            if notes:
                grid.addWidget(label("\n".join(notes), "small", wrap=True), row, 1)
                row += 1
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 170)
        layout.addLayout(grid)
        if step_type == ALLOW_RULES:
            clear = button(t("Clear callsigns and radius"), variant="secondary", on_click=lambda value=step: self._clear_allow_rules(value))
            clear.setEnabled(bool(config.get("callsigns")) or bool(str(config.get("radius_km") or "").strip()))
            clear_row = QHBoxLayout()
            clear_row.addWidget(clear)
            clear_row.addStretch(1)
            layout.addLayout(clear_row)
        return editor

    def _field_widget(self, step: dict[str, Any], config: dict[str, Any], field: dict[str, Any]) -> QWidget:
        name = str(field.get("name") or "")
        field_type = str(field.get("type") or "text")
        step_type = str(step.get("step_type") or "")
        if step_type == "filter_distance" and field_type == "distance_zones":
            return self._distance_zones_widget(step)
        if step_type == "filter_icon" and name == "icons":
            return self._icons_widget(step, name)
        if field_type == "select":
            widget = QComboBox()
            for option in field.get("options") or ():
                widget.addItem(mode_label(str(option)) if name == "mode" else str(option), str(option))
            current = str(config.get(name) if config.get(name) is not None else "")
            index = widget.findData(current)
            if index < 0 and widget.count():
                index = 0
                config[name] = widget.itemData(0)
            widget.setCurrentIndex(index)
            widget.currentIndexChanged.connect(lambda _index, combo=widget: (config.__setitem__(name, combo.currentData()), self.render()))
            return widget
        if field_type == "textarea":
            widget = QPlainTextEdit()
            widget.setFixedHeight(96)
            raw = config.get(name)
            widget.setPlainText(str(raw or "") if name == "rate_limit_rules_text" else lines_to_text(raw))
            if field.get("placeholder"):
                widget.setPlaceholderText(str(field.get("placeholder")))

            def changed(edit: QPlainTextEdit = widget) -> None:
                text = edit.toPlainText()
                config[name] = text if name == "rate_limit_rules_text" else text_to_lines(text)

            widget.textChanged.connect(changed)
            return widget
        widget = line_edit("" if config.get(name) is None else config.get(name), placeholder=str(field.get("placeholder") or ""))
        if field_type == "number":
            from PySide6.QtGui import QDoubleValidator

            validator = QDoubleValidator(widget)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            widget.setValidator(validator)
        widget.textEdited.connect(lambda text: config.__setitem__(name, text))
        return widget

    def _distance_zones_widget(self, step: dict[str, Any]) -> QWidget:
        zones = ensure_distance_config(step)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        colors = current_colors()
        for index, zone in enumerate(zones):
            frame = QFrame()
            frame.setObjectName("ZoneCard")
            frame.setStyleSheet(f"QFrame#ZoneCard {{ border: 1px solid {colors.css('border')}; border-radius: 6px; }}")
            zone_layout = QVBoxLayout(frame)
            zone_layout.setContentsMargins(10, 8, 10, 8)
            heading = QHBoxLayout()
            heading.addWidget(label(f"{t('Zone')} #{index + 1}", "h3"))
            heading.addStretch(1)
            heading.addWidget(button(t("Get location"), variant="secondary", icon_name="map-marker-plus-outline",
                                     on_click=lambda value=index: self._pick_zone_location(step, value)))
            if len(zones) > 1:
                heading.addWidget(icon_button("delete-outline", f"{t('Remove zone')} #{index + 1}",
                                              lambda value=index: self._remove_zone(step, value), color=colors.color("danger")))
            zone_layout.addLayout(heading)
            fields = QHBoxLayout()
            for key, caption in (("latitude", t("Latitude")), ("longitude", t("Longitude")), ("radius_km", t("Radius (km)"))):
                column = QVBoxLayout()
                column.addWidget(label(caption, "small"))
                edit = line_edit(zone.get(key) or "")
                edit.textEdited.connect(lambda text, current=zone, field=key: current.__setitem__(field, text))
                column.addWidget(edit)
                fields.addLayout(column)
            zone_layout.addLayout(fields)
            layout.addWidget(frame)
        if len(zones) < 3:
            add_row = QHBoxLayout()
            add_row.addWidget(button(t("Add zone"), variant="secondary", icon_name="plus", on_click=lambda: self._add_zone(step)))
            add_row.addStretch(1)
            layout.addLayout(add_row)
        return host

    def _icons_widget(self, step: dict[str, Any], name: str) -> QWidget:
        config = step.setdefault("config", {})
        config[name] = unique_lines(config.get(name) or [])
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        controls = QHBoxLayout()
        picker = SymbolPicker(with_overlay=False)
        controls.addWidget(picker, 1)
        add = button(t("Add"), variant="secondary", icon_name="plus")
        controls.addWidget(add, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(controls)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        if not config[name]:
            chips.addWidget(label(t("none"), "muted"))
        for value in config[name]:
            chip_widget = QFrame()
            chip_widget.setObjectName("IconChip")
            chip_widget.setStyleSheet(f"QFrame#IconChip {{ border: 1px solid {current_colors().css('border')}; border-radius: 12px; }}")
            chip_layout = QHBoxLayout(chip_widget)
            chip_layout.setContentsMargins(6, 2, 2, 2)
            chip_layout.setSpacing(4)
            preview = QLabel()
            table, code = (value[:1], value[1:2]) if len(value) >= 2 else ("/", value[:1])
            preview.setPixmap(symbol_pixmap(symbol_icon_for(table, code), "", 18))
            chip_layout.addWidget(preview)
            chip_layout.addWidget(label(value, "mono"))
            remove = QToolButton()
            remove.setProperty("variant", "icon")
            remove.setIcon(icon("close-circle-outline", None, 14))
            remove.setIconSize(QSize(14, 14))
            remove.setToolTip(f"{t('Delete')}: {value}")
            remove.clicked.connect(lambda _checked=False, current=value: self._remove_icon(step, name, current))
            chip_layout.addWidget(remove)
            chips.addWidget(chip_widget)
        chips.addStretch(1)
        layout.addLayout(chips)

        def add_icon() -> None:
            values = picker.values()
            config[name] = unique_lines([*config.get(name, []), f"{values['symbol_table']}{values['symbol_code']}"])
            self.render()

        add.clicked.connect(lambda _checked=False: add_icon())
        return host

    # ============================================================ step edits
    def _set_step_enabled(self, step: dict[str, Any], checked: bool) -> None:
        step["enabled"] = 1 if checked else 0
        self.render()

    def _toggle_expanded(self, index: int) -> None:
        self.expanded = -1 if self.expanded == index else index
        self.render()

    def _move_step(self, index: int, delta: int) -> None:
        target = index + delta
        if not (0 < index < len(self.steps) - 1 and 0 < target < len(self.steps) - 1):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self.expanded = target
        self.render()

    def _delete_step(self, index: int) -> None:
        if 0 < index < len(self.steps) - 1:
            del self.steps[index]
            self.expanded = -1
            self.render()

    def _clear_allow_rules(self, step: dict[str, Any]) -> None:
        step.setdefault("config", {}).update({"callsigns": [], "radius_km": ""})
        self.render()

    def _add_zone(self, step: dict[str, Any]) -> None:
        zones = ensure_distance_config(step)
        if len(zones) < 3:
            zones.append({"latitude": "", "longitude": "", "radius_km": ""})
        self.render()

    def _remove_zone(self, step: dict[str, Any], index: int) -> None:
        zones = ensure_distance_config(step)
        step["config"] = {"zones": [zone for position, zone in enumerate(zones) if position != index]}
        self.render()

    def _pick_zone_location(self, step: dict[str, Any], index: int) -> None:
        zones = ensure_distance_config(step)
        if not 0 <= index < len(zones):
            return
        zone = zones[index]
        picked = LocationPickerDialog.pick(self, zone.get("latitude"), zone.get("longitude"))
        if picked is not None:
            zone["latitude"] = f"{picked[0]:.5f}"
            zone["longitude"] = f"{picked[1]:.5f}"
            self.render()

    def _remove_icon(self, step: dict[str, Any], name: str, value: str) -> None:
        config = step.setdefault("config", {})
        config[name] = [item for item in config.get(name) or [] if item != value]
        self.render()

    # ================================================================== save
    def payload(self) -> dict[str, Any]:
        self._apply_rules()
        for step in self.steps:
            if step.get("step_type") == "filter_distance":
                step["config"] = {"zones": normalize_distance_zones((step.get("config") or {}).get("zones"))}
        source_selector = str(self.source_combo.currentData() or "")
        target_selector = str(self.target_combo.currentData() or "")
        if "::" not in source_selector:
            raise ValueError("Source interface is required.")
        if "::" not in target_selector:
            raise ValueError("Target interface is required.")
        source_kind, source_ref = source_selector.split("::", 1)
        target_kind, target_ref = target_selector.split("::", 1)
        steps = [
            {"id": step.get("id") or None, "step_type": step.get("step_type"), "title": step.get("title"),
             "enabled": 1 if step.get("enabled") else 0, "config": step.get("config") or {}}
            for step in self.steps
        ]
        # Same round trip as the web form (steps_json) so values are plain JSON types.
        steps = json.loads(json.dumps(steps))
        return {
            "name": self.name.text().strip(),
            "description": self.description.text().strip(),
            "source_selector": source_selector,
            "target_selector": target_selector,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "target_kind": target_kind,
            "target_ref": target_ref,
            "enabled": 1 if self.enabled.isChecked() else 0,
            "steps": steps,
        }

    def _save(self) -> None:
        from app.services.digi_flows import safe_create_digi_flow, safe_update_digi_flow

        try:
            payload = self.payload()
        except ValueError as exc:
            self.flash.error(t(str(exc)))
            return
        if self.flow_id is None:
            flow_id, error = safe_create_digi_flow(payload)
            message = "Packet Routing flow created."
        else:
            error = safe_update_digi_flow(self.flow_id, payload)
            flow_id = self.flow_id
            message = "Packet Routing flow updated."
        if error or flow_id is None:
            self.flash.error(t(error or "Operation failed."))
            return
        self.context.notify_data_changed("digi-flows")
        self.open_argument({"id": flow_id, "flash": message})

    # ============================================================ executions
    def refresh(self) -> None:
        if self.flow_id is not None and not self.executions_frozen:
            self.execution_loader.request()

    def _load_executions(self) -> tuple[int | None, list[dict[str, Any]]]:
        from app.services.digi_flows import get_digi_flow_execution_summaries

        flow_id = self.flow_id
        if flow_id is None:
            return None, []
        return flow_id, get_digi_flow_execution_summaries(flow_id, execution_limit=10)

    def _apply_executions(self, result: tuple[int | None, list[dict[str, Any]]]) -> None:
        flow_id, executions = result
        if flow_id != self.flow_id or self.executions_frozen:
            return
        signature = json.dumps([(item.get("frame_uid"), item.get("final_result"), [step.get("status") for step in item.get("steps") or []])
                                for item in executions], default=str)
        if signature == self._execution_signature:
            return
        self._execution_signature = signature
        clear_layout(self.executions_host)
        self.executions_empty.setVisible(not executions)
        for execution in executions:
            self.executions_host.addWidget(self._execution_card(execution))

    def _toggle_freeze(self) -> None:
        self.executions_frozen = not self.executions_frozen
        self.freeze_button.setText(t("Release") if self.executions_frozen else t("Freeze"))
        self.freeze_button.setIcon(icon("play" if self.executions_frozen else "pause", None, 16))
        if not self.executions_frozen:
            self.refresh()

    def _execution_card(self, execution: dict[str, Any]) -> QWidget:
        colors = current_colors()
        frame = QFrame()
        frame.setObjectName("ExecutionCard")
        frame.setStyleSheet(f"QFrame#ExecutionCard {{ background: {colors.css('panel-alt')}; border: 1px solid {colors.css('border')}; border-radius: 8px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        result = str(execution.get("final_result") or "")
        tone = "ok" if result in {"LOGGED", "TX"} else ("error" if result in {"DROPPED", "REJECTED"} else "neutral")
        top = QHBoxLayout()
        top.addWidget(Badge(t(result), tone))
        top.addWidget(label(str(execution.get("display_created_at") or execution.get("created_at") or ""), "mono"))
        top.addWidget(label(tf("Flow {count} steps", count=execution.get("step_count") or 0), "muted"))
        top.addStretch(1)
        layout.addLayout(top)
        for icon_name, caption, value in (
            ("login", t("Input packet"), execution.get("raw_packet") or "-"),
            ("send-variant", t("Output packet"), execution.get("processed_packet") or execution.get("raw_packet") or "-"),
        ):
            row = QHBoxLayout()
            marker = QLabel()
            from app.desktop.icons import icon_pixmap

            marker.setPixmap(icon_pixmap(icon_name, colors.color("muted"), 16))
            marker.setToolTip(caption)
            row.addWidget(marker)
            packet = label(str(value), "mono", wrap=True, selectable=True)
            row.addWidget(packet, 1)
            layout.addLayout(row)
        finished = (f"{t('step')} {execution.get('final_step_number')} {execution.get('final_step_title') or ''}"
                    if execution.get("final_step_number") else "-")
        layout.addWidget(label(
            f"{t('Source:')} {execution.get('source_display') or '-'}   ·   UID: {execution.get('frame_uid') or ''}   ·   "
            f"{t('Pipeline:')} {execution.get('step_path') or ''}   ·   {t('Finished:')} {finished}", "small", wrap=True, selectable=True))
        if execution.get("layout_changed") and execution.get("layout_note"):
            layout.addWidget(label(str(execution.get("layout_note")), "warning", wrap=True))
        steps_grid = QGridLayout()
        steps_grid.setHorizontalSpacing(10)
        steps_grid.setVerticalSpacing(2)
        for row_index, step in enumerate(execution.get("steps") or []):
            status = str(step.get("status") or "")
            status_tone = "ok" if status in {"passed", "executed"} else ("error" if status == "rejected" else "neutral")
            steps_grid.addWidget(label(str(step.get("number") or ""), "small"), row_index, 0)
            name = label(str(step.get("title") or ""), None)
            name.setStyleSheet("font-weight: 600;")
            steps_grid.addWidget(name, row_index, 1)
            steps_grid.addWidget(Badge(t(status), status_tone), row_index, 2, Qt.AlignmentFlag.AlignLeft)
            steps_grid.addWidget(label(str(step.get("description") or "-"), "small", wrap=True), row_index, 3)
        steps_grid.setColumnStretch(3, 1)
        layout.addLayout(steps_grid)
        return frame

