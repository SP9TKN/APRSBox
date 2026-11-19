"""Location picker, PHG generator and APRS symbol picker (shared by several pages)."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.desktop.i18n import t
from app.desktop.icons import symbol_pixmap
from app.desktop.map_layers import parse_float
from app.desktop.widgets.common import button, label
from app.desktop.widgets.map_view import MapView


class _CrosshairMap(MapView):
    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        for color, width in ((QColor(255, 255, 255, 230), 4), (QColor("#d9480f"), 2)):
            painter.setPen(QPen(color, width))
            painter.drawLine(QPointF(center.x() - 14, center.y()), QPointF(center.x() - 4, center.y()))
            painter.drawLine(QPointF(center.x() + 4, center.y()), QPointF(center.x() + 14, center.y()))
            painter.drawLine(QPointF(center.x(), center.y() - 14), QPointF(center.x(), center.y() - 4))
            painter.drawLine(QPointF(center.x(), center.y() + 4), QPointF(center.x(), center.y() + 14))
        painter.end()


class LocationPickerDialog(QDialog):
    """Web: .location-picker-modal - pan the map under the crosshair, then apply."""

    def __init__(self, parent: QWidget | None = None, *, latitude: Any = None, longitude: Any = None,
                 title: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or t("Get location"))
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.map = _CrosshairMap(self)
        self.map.reload_tile_source()
        layout.addWidget(self.map, 1)
        footer = QHBoxLayout()
        self.center_label = label("--", "small")
        footer.addWidget(self.center_label, 1)
        footer.addWidget(button(t("Cancel"), variant="secondary", on_click=self.reject))
        footer.addWidget(button(t("Get coordinates"), on_click=self.accept))
        layout.addLayout(footer)
        self.map.view_changed.connect(self._update_label)
        self.map.map_clicked.connect(lambda lat, lon: self.map.set_view(lat, lon))
        start_lat = parse_float(latitude)
        start_lon = parse_float(longitude)
        zoom = 13
        if start_lat is None or start_lon is None:
            from app.services.map_service import get_map_page_config

            try:
                config = get_map_page_config()
                start_lat = parse_float(config.get("station_latitude")) or 52.0
                start_lon = parse_float(config.get("station_longitude")) or 19.0
                zoom = int(config.get("default_zoom") or 10)
            except Exception:
                start_lat, start_lon, zoom = 52.0, 19.0, 6
        self.map.set_view(start_lat, start_lon, zoom)

    def _update_label(self, latitude: float, longitude: float, _zoom: int) -> None:
        self.center_label.setText(f"{latitude:.5f}, {longitude:.5f}")

    def coordinates(self) -> tuple[float, float]:
        return round(self.map.center_lat, 5), round(self.map.center_lon, 5)

    @staticmethod
    def pick(parent: QWidget | None, latitude: Any = None, longitude: Any = None) -> tuple[float, float] | None:
        dialog = LocationPickerDialog(parent, latitude=latitude, longitude=longitude)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.coordinates()
        return None


POWER_WATTS_BY_CODE = [code * code for code in range(10)]
HEIGHT_FEET_BY_CODE = [10 * (2 ** code) for code in range(10)]


def _nearest(target: float, values: list[float]) -> int:
    best_index = 0
    best_distance = math.inf
    for index, candidate in enumerate(values):
        distance = abs(candidate - target)
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def build_phg(power_w: float, height: float, gain_dbi: float, direction: int, *, imperial: bool = False) -> str:
    """Port of station.html PHG generator (nearest power/height codes)."""
    power_code = _nearest(max(0.0, power_w), POWER_WATTS_BY_CODE)
    height_feet = height if imperial else height * 3.28084
    height_code = _nearest(max(0.0, height_feet), HEIGHT_FEET_BY_CODE)
    gain_code = max(0, min(9, int(round(max(0.0, gain_dbi)))))
    direction_code = max(0, min(9, int(direction)))
    return f"PHG{power_code}{height_code}{gain_code}{direction_code}"


def insert_phg(comment: str, token: str) -> str:
    import re

    rest = re.sub(r"^\s*PHG[0-9A-Z]{4}\s*", "", str(comment or ""), flags=re.IGNORECASE)
    return f"{token} {rest}".strip() if rest else token


class PhgGeneratorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, comment: str = "", imperial: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("PHG Generator"))
        self.imperial = imperial
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.addWidget(label(t("PHG Generator"), "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.power = QDoubleSpinBox()
        self.power.setRange(0, 10000)
        self.power.setDecimals(0)
        self.height = QDoubleSpinBox()
        self.height.setRange(0, 10000)
        self.height.setDecimals(0 if imperial else 1)
        self.gain = QSpinBox()
        self.gain.setRange(0, 9)
        self.direction = QComboBox()
        for value, caption in ((0, t("Omni")), (1, "NE (45°)"), (2, "E (90°)"), (3, "SE (135°)"), (4, "S (180°)"),
                               (5, "SW (225°)"), (6, "W (270°)"), (7, "NW (315°)"), (8, "N (360°)"), (9, "9")):
            self.direction.addItem(caption, value)
        grid.addWidget(label(f"{t('Power')} (W)"), 0, 0)
        grid.addWidget(self.power, 1, 0)
        grid.addWidget(label(f"{t('Height above ground')} ({'ft' if imperial else 'm'})"), 0, 1)
        grid.addWidget(self.height, 1, 1)
        grid.addWidget(label(f"{t('Gain')} (dBi)"), 2, 0)
        grid.addWidget(self.gain, 3, 0)
        grid.addWidget(label(t("Direction")), 2, 1)
        grid.addWidget(self.direction, 3, 1)
        layout.addLayout(grid)
        preview = QFrame()
        preview.setProperty("tile", True)
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(label(t("Generated PHG"), "small"))
        self.preview_code = label("PHG0000", "kpi-small", selectable=True)
        self.preview_details = label("", "small")
        preview_layout.addWidget(self.preview_code)
        preview_layout.addWidget(self.preview_details)
        layout.addWidget(preview)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(button(t("Cancel"), variant="secondary", on_click=self.reject))
        footer.addWidget(button(t("Insert PHG"), on_click=self.accept))
        layout.addLayout(footer)
        for widget in (self.power, self.height):
            widget.valueChanged.connect(self._update)
        self.gain.valueChanged.connect(self._update)
        self.direction.currentIndexChanged.connect(self._update)
        self._prefill(comment)
        self._update()

    def _prefill(self, comment: str) -> None:
        import re

        match = re.match(r"^\s*PHG([0-9])([0-9])([0-9])([0-9])\b", str(comment or ""), re.IGNORECASE)
        if not match:
            return
        power_code, height_code, gain_code, direction_code = (int(value) for value in match.groups())
        self.power.setValue(POWER_WATTS_BY_CODE[power_code])
        feet = HEIGHT_FEET_BY_CODE[height_code]
        self.height.setValue(feet if self.imperial else round(feet / 3.28084, 1))
        self.gain.setValue(gain_code)
        index = self.direction.findData(direction_code)
        if index >= 0:
            self.direction.setCurrentIndex(index)

    def token(self) -> str:
        return build_phg(self.power.value(), self.height.value(), self.gain.value(), int(self.direction.currentData() or 0),
                         imperial=self.imperial)

    def _update(self) -> None:
        token = self.token()
        self.preview_code.setText(token)
        power = POWER_WATTS_BY_CODE[int(token[3])]
        feet = HEIGHT_FEET_BY_CODE[int(token[4])]
        height_text = f"{feet} ft" if self.imperial else f"{round(feet / 3.28084, 1)} m"
        self.preview_details.setText(f"{power} W · {height_text} · {token[5]} dBi · {self.direction.currentText()}")


class SymbolPicker(QWidget):
    """Symbol table + code (icons with descriptions) + overlay (web: station-symbol-picker)."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None, *, with_overlay: bool = True) -> None:
        super().__init__(parent)
        from app.aprs_symbols import get_aprs_symbol_description

        self._describe = get_aprs_symbol_description
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(4)
        self.table = QComboBox()
        self.table.addItem(t("Primary (/)"), "/")
        self.table.addItem(t("Alternate (\\)"), "\\")
        self.code = QComboBox()
        self.code.setIconSize(QSize(22, 22))
        self.code.setMaxVisibleItems(16)
        self.overlay = QComboBox()
        self.overlay.addItem(t("None"), "")
        for value in [str(index) for index in range(10)] + [chr(code) for code in range(ord("A"), ord("Z") + 1)]:
            self.overlay.addItem(value, value)
        layout.addWidget(label(t("Symbol Table")), 0, 0)
        layout.addWidget(label(t("Symbol Code")), 0, 1)
        layout.addWidget(self.table, 1, 0)
        layout.addWidget(self.code, 1, 1)
        if with_overlay:
            layout.addWidget(label(t("Overlay")), 0, 2)
            layout.addWidget(self.overlay, 1, 2)
        else:
            self.overlay.hide()
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(2, 1)
        self.table.currentIndexChanged.connect(self._table_changed)
        self.code.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.overlay.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self._fill_codes()

    def _fill_codes(self) -> None:
        from app.desktop.icons import symbol_icon_for

        table = str(self.table.currentData() or "/")
        current = self.code.currentData()
        self.code.blockSignals(True)
        self.code.clear()
        for code in range(33, 127):
            char = chr(code)
            description = self._describe(table, char) or ""
            from PySide6.QtGui import QIcon

            pixmap = symbol_pixmap(symbol_icon_for(table, char), "", 22)
            self.code.addItem(QIcon(pixmap), f"{char}  {description}".rstrip(), char)
        if current is not None:
            index = self.code.findData(current)
            self.code.setCurrentIndex(index if index >= 0 else 0)
        self.code.blockSignals(False)

    def _table_changed(self) -> None:
        self._fill_codes()
        self.overlay.setEnabled(self.table.currentData() == "\\")
        if self.table.currentData() != "\\":
            self.overlay.setCurrentIndex(0)
        self.changed.emit()

    def set_value(self, table: Any, code: Any, overlay: Any = "") -> None:
        table_text = "\\" if str(table or "/") == "\\" else "/"
        self.table.blockSignals(True)
        self.table.setCurrentIndex(self.table.findData(table_text))
        self.table.blockSignals(False)
        self._fill_codes()
        index = self.code.findData(str(code or ">")[:1])
        self.code.setCurrentIndex(index if index >= 0 else self.code.findData(">"))
        overlay_index = self.overlay.findData(str(overlay or ""))
        self.overlay.setCurrentIndex(overlay_index if overlay_index >= 0 else 0)
        self.overlay.setEnabled(table_text == "\\")

    def values(self) -> dict[str, str]:
        return {
            "symbol_table": str(self.table.currentData() or "/"),
            "symbol_code": str(self.code.currentData() or ">"),
            "symbol_overlay": str(self.overlay.currentData() or "") if self.table.currentData() == "\\" else "",
        }


class CharCounterLabel(QLabel):
    """'(12 / 43)' counter next to a limited text field (web: field-counter-inline)."""

    def __init__(self, maximum: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.maximum = maximum
        self.setProperty("role", "small")

    def update_count(self, text: str) -> None:
        count = len(text or "")
        self.setText(f"({count} / {self.maximum})")
        from app.desktop.widgets.common import set_prop

        set_prop(self, "role", "error" if count >= self.maximum else "small")
