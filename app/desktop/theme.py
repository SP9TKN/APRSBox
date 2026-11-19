"""Window styling: the web palettes rendered as a Qt style sheet."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from app.desktop.palettes import PALETTES
from app.ui_palette import DEFAULT_UI_PALETTE, normalize_ui_palette

THEME_DARK = "dark"
THEME_LIGHT = "light"

UI_FONT_FAMILY = "Segoe UI"
MONO_FONT_FAMILY = "Consolas"


def parse_css_color(text: str | None, fallback: str = "#000000") -> QColor:
    value = str(text or "").strip()
    match = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if match:
        parts = [part.strip() for part in match.group(1).split(",")]
        try:
            color = QColor(int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
            if len(parts) > 3:
                color.setAlphaF(max(0.0, min(1.0, float(parts[3]))))
            return color
        except (ValueError, IndexError):
            return QColor(fallback)
    color = QColor(value)
    return color if color.isValid() else QColor(fallback)


def blend(top: QColor, bottom: QColor) -> QColor:
    """Flatten a translucent color over an opaque one."""
    alpha = top.alphaF()
    return QColor(
        round(top.red() * alpha + bottom.red() * (1 - alpha)),
        round(top.green() * alpha + bottom.green() * (1 - alpha)),
        round(top.blue() * alpha + bottom.blue() * (1 - alpha)),
    )


def mix(first: QColor, second: QColor, ratio: float) -> QColor:
    """``ratio`` of ``first`` and ``1 - ratio`` of ``second`` (both opaque)."""
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        round(first.red() * ratio + second.red() * (1 - ratio)),
        round(first.green() * ratio + second.green() * (1 - ratio)),
        round(first.blue() * ratio + second.blue() * (1 - ratio)),
    )


def luminance(color: QColor) -> float:
    """WCAG relative luminance."""

    def channel(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def contrast_ratio(first: QColor, second: QColor) -> float:
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@dataclass(slots=True)
class ThemeColors:
    palette_name: str
    mode: str
    raw: dict[str, str] = field(default_factory=dict)

    def color(self, key: str, fallback: str = "#808080") -> QColor:
        """Opaque color for ``key`` (translucent tokens are flattened over bg)."""
        base = parse_css_color(self.raw.get("bg"), "#0f1311")
        value = parse_css_color(self.raw.get(key), fallback)
        if value.alpha() < 255:
            panel = parse_css_color(self.raw.get("panel"), "#181f1b")
            panel = blend(panel, base) if panel.alpha() < 255 else panel
            return blend(value, panel if key not in {"panel", "sidebar"} else base)
        return value

    def css(self, key: str, fallback: str = "#808080") -> str:
        return self.color(key, fallback).name()

    def translucent(self, key: str, alpha: float, fallback: str = "#808080") -> QColor:
        color = parse_css_color(self.raw.get(key), fallback)
        color.setAlphaF(alpha)
        return color

    @property
    def is_dark(self) -> bool:
        return self.mode == THEME_DARK


_CURRENT: ThemeColors | None = None


def theme_colors(palette_name: str | None = None, mode: str | None = None) -> ThemeColors:
    palette = normalize_ui_palette(palette_name)
    resolved_mode = THEME_LIGHT if str(mode or "").lower() == THEME_LIGHT else THEME_DARK
    raw = dict(PALETTES.get(palette, PALETTES[DEFAULT_UI_PALETTE])[resolved_mode])
    return ThemeColors(palette_name=palette, mode=resolved_mode, raw=raw)


ROW_SELECTION_MIN_CONTRAST = 1.45
TEXT_MIN_CONTRAST = 4.5


def row_selection_color(colors: ThemeColors) -> QColor:
    """Background of a selected table/list row.

    The palettes' accent-soft is almost the zebra color (panel-alt) in every palette, so a
    selected row looked like every second row.  Strengthen it with the accent until it stands
    out from both row colors, but never past the point where the text gets hard to read.
    """
    panel, panel_alt, text, accent = (colors.color(key) for key in ("panel", "panel-alt", "text", "accent"))
    candidate = colors.color("accent-soft")
    ratio = 0.2
    while min(contrast_ratio(candidate, panel), contrast_ratio(candidate, panel_alt)) < ROW_SELECTION_MIN_CONTRAST and ratio <= 0.6:
        stronger = mix(accent, panel, ratio)
        if contrast_ratio(text, stronger) < TEXT_MIN_CONTRAST:
            break
        candidate = stronger
        ratio += 0.05
    return candidate


def current_colors() -> ThemeColors:
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = theme_colors()
    return _CURRENT


def apply_theme(app: QApplication, palette_name: str | None, mode: str | None) -> ThemeColors:
    global _CURRENT
    colors = theme_colors(palette_name, mode)
    _CURRENT = colors
    app.setStyle("Fusion")
    app.setFont(QFont(UI_FONT_FAMILY, 10))
    app.setPalette(build_qpalette(colors))
    app.setStyleSheet(build_stylesheet(colors))
    return colors


def build_qpalette(colors: ThemeColors) -> QPalette:
    palette = QPalette()
    bg = colors.color("bg-layer")
    panel = colors.color("panel")
    surface = colors.color("surface")
    text = colors.color("text")
    muted = colors.color("muted")
    accent = colors.color("accent")
    palette.setColor(QPalette.ColorRole.Window, bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, surface)
    palette.setColor(QPalette.ColorRole.AlternateBase, panel)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, colors.color("panel-alt"))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, colors.color("panel-emphasis"))
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, muted)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, _on_color(accent))
    palette.setColor(QPalette.ColorRole.Link, accent)
    palette.setColor(QPalette.ColorRole.LinkVisited, colors.color("accent-strong"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return palette


def _on_color(background: QColor) -> QColor:
    luminance = 0.2126 * background.redF() + 0.7152 * background.greenF() + 0.0722 * background.blueF()
    return QColor("#0b0f0c") if luminance > 0.45 else QColor("#ffffff")


def on_color(background: QColor) -> QColor:
    return _on_color(background)


def _tinted_icon_file(name: str, color: QColor, size: int = 14) -> str:
    """Tinted PNG copy of a static SVG icon (style sheets can only reference files)."""
    from pathlib import Path

    from app.config import settings

    target_dir = Path(settings.cache_dir) / "desktop-ui"
    target = target_dir / f"{name}-{color.name().lstrip('#')}-{size}.png"
    if not target.exists():
        try:
            from app.desktop.icons import icon_pixmap

            target_dir.mkdir(parents=True, exist_ok=True)
            pixmap = icon_pixmap(name, color, size, ratio=2.0)
            pixmap.save(str(target), "PNG")
        except Exception:
            return ""
    return target.as_posix()


def _check_icon_path(color: QColor) -> str:
    return _tinted_icon_file("check-bold", color, 14)


def build_stylesheet(colors: ThemeColors) -> str:
    c = colors.css
    bg = c("bg-layer")
    panel = c("panel")
    panel_alt = c("panel-alt")
    panel_soft = c("panel-soft")
    emphasis = c("panel-emphasis")
    border = c("border")
    text = c("text")
    muted = c("muted")
    surface = c("surface")
    accent = c("accent")
    accent_strong = c("accent-strong")
    accent_text = _on_color(colors.color("accent")).name()
    accent_soft = colors.color("accent-soft").name()
    row_selected = row_selection_color(colors).name()
    hover = colors.color("hover-layer").name()
    border_hover = colors.color("border-hover").name()
    danger = c("danger")
    success = c("success")
    warning = c("warning")
    sidebar = c("sidebar")
    nav_selected = colors.css("nav-selection-bg") if "nav-selection-bg" in colors.raw else accent_soft
    nav_selected_border = (
        colors.css("nav-selection-border") if "nav-selection-border" in colors.raw else colors.color("border-hover").name()
    )
    secondary = c("secondary")
    code = colors.color("code").name()
    check_icon = _check_icon_path(_on_color(colors.color("accent")))
    arrow_down = _tinted_icon_file("menu-down", colors.color("muted"), 18)
    arrow_up = _tinted_icon_file("menu-up", colors.color("muted"), 18)
    calendar_icon = _tinted_icon_file("calendar-month-outline", colors.color("muted"), 18)
    danger_soft = mix(colors.color("danger"), colors.color("panel"), 0.16).name()
    warning_soft = mix(colors.color("warning"), colors.color("panel"), 0.16).name()
    success_soft = mix(colors.color("success"), colors.color("panel"), 0.16).name()
    return f"""
* {{
    outline: none;
}}
QWidget {{
    color: {text};
    font-family: "{UI_FONT_FAMILY}";
    font-size: 10pt;
}}
QMainWindow, QDialog, QWidget#PageHost, QStackedWidget#Pages {{
    background: {bg};
}}
QScrollArea, QScrollArea > QWidget > QWidget#PageBody {{
    background: transparent;
    border: none;
}}
QWidget#PageBody {{
    background: {bg};
}}
QFrame#MapTopBar {{
    background: {bg};
    border: none;
    border-bottom: 1px solid {border};
}}
QWidget#Sidebar {{
    background: {sidebar};
    border-right: 1px solid {border};
}}
QLabel#BrandTitle {{
    font-size: 14pt;
    font-weight: 700;
    color: {text};
}}
QLabel#BrandSubtitle {{
    color: {muted};
    font-size: 9pt;
}}
QFrame#UserBox {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 8px;
}}
QLabel#RoleBadge {{
    background: {accent_soft};
    color: {accent};
    border: 1px solid {border_hover};
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 600;
}}
QLabel#UtcClock {{
    color: {muted};
    font-family: "{MONO_FONT_FAMILY}";
    padding: 2px 4px;
}}
QListWidget#NavList {{
    background: transparent;
    border: none;
    padding: 0px;
}}
QListWidget#NavList::item {{
    color: {text};
    padding: 7px 8px;
    margin: 1px 0px;
    border-radius: 8px;
    border: 1px solid transparent;
}}
QListWidget#NavList::item:hover {{
    background: {hover};
}}
QListWidget#NavList::item:selected {{
    background: {nav_selected};
    border: 1px solid {nav_selected_border};
    color: {text};
}}
QListWidget#NavList::item:disabled {{
    color: {muted};
}}
QLabel#SidebarFooter {{
    color: {muted};
    font-size: 8pt;
}}
QFrame[card="true"] {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 8px;
}}
QFrame[card="soft"] {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 8px;
}}
QFrame[card="inner"] {{
    background: transparent;
    border: none;
}}
QFrame[tile="true"] {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 8px;
}}
QFrame[tone="accent"] {{
    border: 1px solid {border_hover};
}}
QFrame[tone="danger"] {{
    background: {danger_soft};
    border: 1px solid {danger};
}}
QFrame[tone="warning"] {{
    background: {warning_soft};
    border: 1px solid {warning};
}}
QFrame[tone="success"] {{
    background: {success_soft};
    border: 1px solid {success};
}}
QFrame#Separator {{
    background: {border};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}
QLabel {{
    background: transparent;
}}
QLabel[role="page-title"] {{
    font-size: 15pt;
    font-weight: 700;
}}
QLabel[role="h2"] {{
    font-size: 12pt;
    font-weight: 700;
}}
QLabel[role="h3"] {{
    font-size: 10.5pt;
    font-weight: 700;
}}
QLabel[role="muted"] {{
    color: {muted};
}}
QLabel[role="small"] {{
    color: {muted};
    font-size: 8.5pt;
}}
QLabel[role="kpi"] {{
    font-size: 18pt;
    font-weight: 700;
}}
QLabel[role="kpi-small"] {{
    font-size: 13pt;
    font-weight: 700;
}}
QLabel[role="eyebrow"] {{
    color: {muted};
    font-size: 8.5pt;
    font-weight: 700;
    text-transform: uppercase;
}}
QLabel[role="mono"] {{
    font-family: "{MONO_FONT_FAMILY}";
}}
QLabel[role="error"] {{
    color: {danger};
}}
QLabel[role="success"] {{
    color: {success};
}}
QLabel[role="warning"] {{
    color: {warning};
}}
QLabel[role="accent"] {{
    color: {accent};
}}
QLabel[badge="true"] {{
    border-radius: 9px;
    padding: 1px 9px;
    font-size: 8.5pt;
    font-weight: 700;
    background: {panel_soft};
    color: {text};
    border: 1px solid {border};
}}
QLabel[badge="true"][tone="ok"], QLabel[badge="true"][tone="success"] {{
    background: {success_soft};
    color: {success};
    border: 1px solid {success};
}}
QLabel[badge="true"][tone="warning"], QLabel[badge="true"][tone="warn"] {{
    background: {warning_soft};
    color: {warning};
    border: 1px solid {warning};
}}
QLabel[badge="true"][tone="danger"], QLabel[badge="true"][tone="error"] {{
    background: {danger_soft};
    color: {danger};
    border: 1px solid {danger};
}}
QLabel[badge="true"][tone="accent"], QLabel[badge="true"][tone="info"] {{
    background: {accent_soft};
    color: {accent};
    border: 1px solid {border_hover};
}}
QLabel[flash="true"] {{
    border-radius: 8px;
    padding: 8px 12px;
    background: {success_soft};
    border: 1px solid {success};
    color: {text};
}}
QLabel[flash="true"][tone="error"] {{
    background: {danger_soft};
    border: 1px solid {danger};
}}
QLabel[flash="true"][tone="info"] {{
    background: {accent_soft};
    border: 1px solid {border_hover};
}}
QPushButton, QToolButton {{
    background: {accent};
    color: {accent_text};
    border: 1px solid {accent_strong};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover, QToolButton:hover {{
    background: {accent_strong};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: {accent_strong};
    padding-top: 7px;
    padding-bottom: 5px;
}}
QPushButton:disabled, QToolButton:disabled {{
    background: {panel_soft};
    color: {muted};
    border: 1px solid {border};
}}
QPushButton[variant="secondary"], QToolButton[variant="secondary"] {{
    background: {panel_alt};
    color: {text};
    border: 1px solid {border};
}}
QPushButton[variant="secondary"]:hover, QToolButton[variant="secondary"]:hover {{
    background: {emphasis};
    border: 1px solid {border_hover};
}}
QPushButton[variant="danger"], QToolButton[variant="danger"] {{
    background: {danger_soft};
    color: {danger};
    border: 1px solid {danger};
}}
QPushButton[variant="danger"]:hover, QToolButton[variant="danger"]:hover {{
    background: {danger};
    color: {_on_color(colors.color("danger")).name()};
}}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{
    background: transparent;
    color: {text};
    border: 1px solid transparent;
    padding: 4px 6px;
}}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{
    background: {hover};
    border: 1px solid {border};
}}
QToolButton[variant="icon"] {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
    min-width: 22px;
    min-height: 22px;
}}
QToolButton[variant="icon"]:hover {{
    background: {emphasis};
    border: 1px solid {border_hover};
}}
QToolButton[variant="icon"]:checked {{
    background: {accent_soft};
    border: 1px solid {border_hover};
}}
QPushButton[variant="link"] {{
    background: transparent;
    border: none;
    color: {accent};
    padding: 0px;
    text-align: left;
    font-weight: 600;
}}
QPushButton[variant="link"]:hover {{
    color: {accent_strong};
    text-decoration: underline;
}}
QPushButton:checked[variant="secondary"] {{
    background: {accent_soft};
    border: 1px solid {border_hover};
    color: {accent};
}}
QPushButton[variant="secondary"]:disabled, QPushButton[variant="danger"]:disabled,
QPushButton[variant="ghost"]:disabled, QToolButton[variant="secondary"]:disabled,
QToolButton[variant="danger"]:disabled {{
    background: {panel_soft};
    color: {muted};
    border: 1px solid {border};
}}
QPushButton[variant="link"]:disabled {{
    color: {muted};
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {{
    background: {surface};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {accent};
    selection-color: {accent_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateTimeEdit:focus, QComboBox:focus {{
    border: 1px solid {accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QDateTimeEdit:disabled, QComboBox:disabled {{
    color: {muted};
    background: {panel_alt};
}}
QLineEdit[invalid="true"] {{
    border: 1px solid {danger};
}}
QLineEdit[readOnly="true"] {{
    background: {panel_alt};
}}
QTextBrowser {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: url("{arrow_down}");
    width: 18px;
    height: 18px;
    margin-right: 4px;
}}
QDateTimeEdit::drop-down {{
    border: none;
    width: 26px;
}}
QDateTimeEdit::down-arrow {{
    image: url("{calendar_icon}");
    width: 16px;
    height: 16px;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {panel};
    border: 1px solid {border};
    selection-background-color: {accent_soft};
    selection-color: {text};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QDateTimeEdit::up-button, QDateTimeEdit::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow {{
    image: url("{arrow_up}");
    width: 14px;
    height: 14px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow {{
    image: url("{arrow_down}");
    width: 14px;
    height: 14px;
}}
QCheckBox, QRadioButton {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {border};
    background: {surface};
}}
QCheckBox::indicator {{
    border-radius: 4px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border: 1px solid {accent};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border: 1px solid {accent_strong};
    image: url("{check_icon}");
}}
QRadioButton::indicator:checked {{
    background: {accent};
    border: 4px solid {surface};
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {muted};
}}
QGroupBox {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0px 4px;
    color: {text};
}}
QTableView, QTreeView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {panel};
    alternate-background-color: {panel_alt};
    border: 1px solid {border};
    border-radius: 8px;
    gridline-color: {border};
    selection-background-color: {row_selected};
    selection-color: {text};
}}
QTableView::item, QTreeView::item {{
    padding: 3px 6px;
    border: none;
    border-top: 1px solid transparent;
    border-bottom: 1px solid transparent;
}}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{
    background: {hover};
}}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{
    background: {row_selected};
    color: {text};
    border-top: 1px solid {accent};
    border-bottom: 1px solid {accent};
}}
QHeaderView {{
    background: transparent;
    border: none;
}}
QHeaderView::section {{
    background: {panel_alt};
    color: {muted};
    border: none;
    border-bottom: 1px solid {border};
    padding: 5px 8px;
    font-weight: 700;
}}
QTableCornerButton::section {{
    background: {panel_alt};
    border: none;
}}
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 8px;
    background: {panel};
    top: -1px;
}}
QTabBar::tab {{
    background: {panel_alt};
    color: {muted};
    border: 1px solid {border};
    border-bottom: none;
    padding: 6px 14px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    background: {panel};
    color: {text};
    border-bottom: 2px solid {accent};
}}
QTabBar::tab:hover {{
    color: {text};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {muted};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {border};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {muted};
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    width: 0px;
    height: 0px;
}}
QToolTip {{
    background: {emphasis};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 4px 6px;
}}
QMenu {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {accent_soft};
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 6px;
}}
QProgressBar {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 6px;
    text-align: center;
    color: {text};
    min-height: 14px;
}}
QProgressBar::chunk {{
    background: {accent};
    border-radius: 5px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {border};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {accent};
    width: 14px;
    margin: -6px 0px;
    border-radius: 7px;
}}
QSplitter::handle {{
    background: {border};
}}
QStatusBar {{
    background: {sidebar};
    color: {muted};
    border-top: 1px solid {border};
}}
QStatusBar QLabel {{
    color: {muted};
    padding: 0px 6px;
}}
QMessageBox {{
    background: {panel};
}}
QPlainTextEdit[role="log"], QTextEdit[role="log"] {{
    font-family: "{MONO_FONT_FAMILY}";
    font-size: 9pt;
    background: {code};
}}
QLabel[role="code"] {{
    font-family: "{MONO_FONT_FAMILY}";
    background: {code};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 4px 6px;
}}
QFrame#SecondaryAccent {{
    background: {secondary};
}}
"""
