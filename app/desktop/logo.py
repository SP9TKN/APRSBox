"""The APRSBox logo (web partial ``logo.html``) rendered with palette colors."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app.config import settings
from app.desktop.theme import ThemeColors

_LOGO_TEMPLATE = Path(settings.templates_dir) / "partials" / "logo.html"


@lru_cache(maxsize=1)
def _logo_source() -> str:
    try:
        return _LOGO_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _resolve_colors(source: str, colors: ThemeColors) -> str:
    def color_for(name: str) -> str:
        if name == "logo-bg":
            return "none"
        if name in colors.raw:
            return colors.color(name).name()
        return colors.color("accent").name()

    def replace(match: re.Match[str]) -> str:
        prop, name = match.group(1), match.group(2)
        return f'{prop}="{color_for(name)}"'

    # style="fill: var(--x);" / style="stop-color: var(--x);" -> plain SVG attributes.
    source = re.sub(r'style="(fill|stroke|stop-color):\s*var\(--([a-z0-9-]+)\);?"', replace, source)
    source = re.sub(
        r'style="fill:\s*var\(--([a-z0-9-]+)\);\s*stroke:\s*var\(--([a-z0-9-]+)\);?"',
        lambda m: f'fill="{color_for(m.group(1))}" stroke="{color_for(m.group(2))}"',
        source,
    )
    return source


def logo_pixmap(colors: ThemeColors, width: int, ratio: float = 2.0) -> QPixmap:
    source = _logo_source()
    if not source:
        return QPixmap()
    svg = _resolve_colors(source, colors)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    default = renderer.defaultSize()
    height = max(1, round(width * default.height() / max(1, default.width())))
    image = QImage(round(width * ratio), round(height * ratio), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap
