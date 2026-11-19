"""Icons: the web GUI's Material Design SVGs and the APRS symbol sets."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app.config import settings

ICONS_DIR = Path(settings.static_dir) / "icons"
MEDIA_DIR = Path(settings.static_dir) / "media"


def static_path(relative: str) -> Path:
    return Path(settings.static_dir) / str(relative or "").lstrip("/").removeprefix("static/")


@lru_cache(maxsize=1024)
def _svg_bytes(name: str) -> bytes | None:
    path = ICONS_DIR / (name if name.endswith(".svg") else f"{name}.svg")
    try:
        return path.read_bytes()
    except OSError:
        return None


@lru_cache(maxsize=2048)
def _tinted_pixmap(name: str, color: str, size: int, ratio: float) -> QPixmap:
    pixels = max(1, round(size * ratio))
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    data = _svg_bytes(name)
    if data:
        renderer = QSvgRenderer(QByteArray(data))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, pixels, pixels))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), QColor(color))
        painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def icon_pixmap(name: str, color: QColor | str, size: int = 18, ratio: float = 2.0) -> QPixmap:
    color_name = color.name(QColor.NameFormat.HexArgb) if isinstance(color, QColor) else str(color)
    return _tinted_pixmap(name, color_name, int(size), float(ratio))


def icon(name: str, color: QColor | str | None = None, size: int = 18) -> QIcon:
    from app.desktop.theme import current_colors

    colors = current_colors()
    normal = color if color is not None else colors.color("text")
    result = QIcon()
    result.addPixmap(icon_pixmap(name, normal, size), QIcon.Mode.Normal)
    disabled = QColor(colors.color("muted"))
    disabled.setAlphaF(0.4)  # clearly dimmer than an enabled muted icon
    result.addPixmap(icon_pixmap(name, disabled, size), QIcon.Mode.Disabled)
    result.addPixmap(icon_pixmap(name, colors.color("accent"), size), QIcon.Mode.Selected)
    return result


def app_icon() -> QIcon:
    result = QIcon()
    for candidate in ("aprs-box-icon.png", "aprs-box-logo.png"):
        path = MEDIA_DIR / candidate
        if path.exists():
            result.addFile(str(path))
    return result


TILE_WHITE_MIN = 225  # background white of the classic symbols (palette white, no anti-aliasing)
TILE_FRAME_MAX = 40   # their dark frame (#040204)


def without_tile_background(image: QImage) -> QImage:
    """Clear the opaque tile behind a classic (verG) symbol.

    Those GIFs are drawn on a white square with a dark 1 px frame and carry no transparent
    colour, so on the map every symbol sat in a white box.  Peel off the dark frame rings and
    clear the white that is reachable from the edges; white enclosed by the symbol's outline
    (car windows, the "6" ...) stays.
    """
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    clear = QColor(0, 0, 0, 0)

    def ring(index: int) -> list[tuple[int, int]]:
        left, top, right, bottom = index, index, width - 1 - index, height - 1 - index
        if left > right or top > bottom:
            return []
        points = {(x, top) for x in range(left, right + 1)} | {(x, bottom) for x in range(left, right + 1)}
        points |= {(left, y) for y in range(top, bottom + 1)} | {(right, y) for y in range(top, bottom + 1)}
        return sorted(points)

    def is_frame(point: tuple[int, int]) -> bool:
        color = image.pixelColor(*point)
        return color.alpha() > 0 and max(color.red(), color.green(), color.blue()) <= TILE_FRAME_MAX

    def is_tile(point: tuple[int, int]) -> bool:
        color = image.pixelColor(*point)
        return color.alpha() > 0 and min(color.red(), color.green(), color.blue()) >= TILE_WHITE_MIN

    peeled = 0
    while peeled < 2:  # the frame is 1 px; allow a 2 px one, never more
        points = ring(peeled)
        if not points or sum(is_frame(point) for point in points) < 0.6 * len(points):
            break
        for point in points:
            if is_frame(point):
                image.setPixelColor(*point, clear)
        peeled += 1
    stack = [point for point in ring(peeled) if is_tile(point)]
    while stack:
        x, y = stack.pop()
        if not (0 <= x < width and 0 <= y < height) or not is_tile((x, y)):
            continue
        image.setPixelColor(x, y, clear)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return image


@lru_cache(maxsize=512)
def _symbol_base_pixmap(relative_path: str) -> QPixmap:
    image = QImage(str(static_path(relative_path)))
    if image.isNull():
        image = QImage(str(ICONS_DIR / "verG" / "x.gif"))
    if not image.isNull() and not image.hasAlphaChannel():
        image = without_tile_background(image)  # classic GIF set; the modern PNGs are transparent
    return QPixmap.fromImage(image)


@lru_cache(maxsize=2048)
def _symbol_pixmap(relative_path: str, overlay: str, size: int, ratio: float, dark_overlay: bool) -> QPixmap:
    base = _symbol_base_pixmap(relative_path)
    pixels = max(1, round(size * ratio))
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not base.isNull():
        painter.drawPixmap(image.rect(), base)
    overlay = str(overlay or "").strip()[:1]
    if overlay:
        font = QFont("Segoe UI", max(6, int(pixels * 0.5)))
        font.setBold(True)
        painter.setFont(font)
        rect = image.rect()
        painter.setPen(QColor("#000000") if not dark_overlay else QColor("#ffffff"))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, overlay)
        painter.setPen(QColor("#ffffff") if not dark_overlay else QColor("#000000"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, overlay)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def symbol_pixmap(icon_path: str | None, overlay: str | None = None, size: int = 22, ratio: float = 2.0) -> QPixmap:
    return _symbol_pixmap(str(icon_path or "icons/verG/x.gif"), str(overlay or ""), int(size), float(ratio), False)


@lru_cache(maxsize=2048)
def symbol_shadow_pixmap(icon_path: str | None, overlay: str | None = None, size: int = 22, ratio: float = 2.0) -> QPixmap:
    """Dark silhouette of a symbol, drawn 1 px lower as a soft shadow (web: drop-shadow on map icons)."""
    source = symbol_pixmap(icon_path, overlay, size, ratio).toImage()
    source.setDevicePixelRatio(1.0)  # copy pixel for pixel; the result gets the ratio back below
    shadow = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
    shadow.fill(Qt.GlobalColor.transparent)
    painter = QPainter(shadow)
    painter.drawImage(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(shadow.rect(), QColor(0, 0, 0, 90))
    painter.end()
    pixmap = QPixmap.fromImage(shadow)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def symbol_icon_for(symbol_table: str | None, symbol_code: str | None, *, symbol_set: str | None = None) -> str:
    """Relative icon path of an APRS symbol (same lookup as the web GUI)."""
    from app.services.content import get_aprs_symbol_icon_path

    table = str(symbol_table or "/")[:1] or "/"
    code = str(symbol_code or "")[:1]
    if not code:
        return "icons/verG/x.gif"
    lookup_table = "/" if table == "/" else "\\"
    return get_aprs_symbol_icon_path(f"{lookup_table}{code}", symbol_set=symbol_set)


def symbol_overlay_for(symbol_table: str | None) -> str:
    table = str(symbol_table or "")[:1]
    if table and table not in {"/", "\\"}:
        return table
    return ""


def clear_caches() -> None:
    _tinted_pixmap.cache_clear()
    _symbol_pixmap.cache_clear()
    symbol_shadow_pixmap.cache_clear()
    _program_icon_pixmap.cache_clear()


def size(value: int) -> QSize:
    return QSize(value, value)


# Sizes Windows asks for: title bar / tray (16-24), taskbar (24-48), Alt+Tab and
# high-DPI screens (up to 256).
PROGRAM_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _draw_overlay(painter: QPainter, rect: Any, overlay: str, pixels: int) -> None:
    font = QFont("Segoe UI", max(6, int(pixels * 0.5)))
    font.setBold(True)
    painter.setFont(font)
    outline = max(1, pixels // 32)
    painter.setPen(QColor("#000000"))
    for dx, dy in ((-outline, 0), (outline, 0), (0, -outline), (0, outline)):
        painter.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, overlay)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, overlay)


@lru_cache(maxsize=256)
def _program_icon_pixmap(relative_path: str, overlay: str, pixels: int) -> QPixmap:
    """One square icon image; pixel-art symbols are enlarged by whole multiples first so big icons stay sharp."""
    base = _symbol_base_pixmap(relative_path)
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if not base.isNull():
        source = base.toImage()
        factor = pixels // max(1, max(source.width(), source.height()))
        if factor >= 2:
            source = source.scaled(source.width() * factor, source.height() * factor,
                                   Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        scaled = source.scaled(pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(image)
        painter.drawImage((pixels - scaled.width()) // 2, (pixels - scaled.height()) // 2, scaled)
        painter.end()
    if overlay:
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        _draw_overlay(painter, image.rect(), overlay, pixels)
        painter.end()
    return QPixmap.fromImage(image)


def station_symbol_icon(symbol_table: str | None, symbol_code: str | None, symbol_overlay: str | None = None, *,
                        symbol_set: str | None = None) -> QIcon:
    """Program icon from the station's APRS symbol (like APRSIS32).

    Station settings keep the table as "/" or "\\" and the overlay character separately.
    """
    table = "\\" if str(symbol_table or "/")[:1] == "\\" else "/"
    overlay = str(symbol_overlay or "").strip()[:1] if table == "\\" else ""
    path = symbol_icon_for(table, symbol_code, symbol_set=symbol_set)
    result = QIcon()
    for pixels in PROGRAM_ICON_SIZES:
        result.addPixmap(_program_icon_pixmap(path, overlay, pixels))
    return result


def program_icon(station: dict[str, Any] | None, *, symbol_set: str | None = None, use_station_symbol: bool = True) -> QIcon:
    """The station symbol once a callsign is configured, otherwise the APRSBox logo."""
    station = station or {}
    if use_station_symbol and str(station.get("callsign") or "").strip() and str(station.get("symbol_code") or "").strip():
        return station_symbol_icon(station.get("symbol_table"), station.get("symbol_code"), station.get("symbol_overlay"),
                                   symbol_set=symbol_set)
    return app_icon()


def program_icon_key(station: dict[str, Any] | None, *, symbol_set: str | None = None, use_station_symbol: bool = True) -> tuple:
    station = station or {}
    return (
        bool(use_station_symbol),
        bool(str(station.get("callsign") or "").strip()),
        str(station.get("symbol_table") or ""),
        str(station.get("symbol_code") or ""),
        str(station.get("symbol_overlay") or ""),
        str(symbol_set or ""),
    )
