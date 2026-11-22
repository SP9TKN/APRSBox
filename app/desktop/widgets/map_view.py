"""Native slippy map (replaces Leaflet in the web GUI).

Features mirror app/static/js/map.js: OSM-style tiles, APRS symbol markers with
callsign labels, marker clustering, spiderfying of overlapping markers, PHG
coverage shapes, mobile tracks, alert area polygons, Maidenhead grid, a
two-point ruler and the darkening mask - plus the desktop-only RF path layer.
"""

from __future__ import annotations

import math
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QToolTip, QWidget

from app.desktop import geo
from app.desktop.icons import symbol_pixmap, symbol_shadow_pixmap
from app.desktop.theme import MONO_FONT_FAMILY, UI_FONT_FAMILY, current_colors, parse_css_color
from app.desktop.tiles import TileManager, shared_tile_manager

MODERN_ICON_PX = 32
LEGACY_SYMBOL_FILE_PX = 22  # classic (verG) symbol files
LEGACY_SYMBOL_ART_PX = 16   # median height of the drawing inside them, once the white tile is removed
# Stations (the user's choice): nothing on hover, the info window on a left click, the station page
# on a double click, the RF path on a right click.  The info window goes on a click, the wheel,
# when the mouse leaves the map, or after this long.
INFO_WINDOW_MS = 60_000
# Callsign bubbles kept drawn (~30 KB each at 2x): the least recently used go first. The map shows a few
# hundred at once; 4000 meant up to ~120 MB on a small PC.
LABEL_CACHE_MAX = 800
# A map off the screen (another page, the window minimized or in the tray) this long gives back its
# drawn labels, and the shared RAM tiles once no map is on the screen (the user: a light app).
RELEASE_UNSEEN_MS = 30_000

# Callsign bubbles in every theme (the user's choice): dark grey text on light grey, the map showing
# through both the bubble and the letters.
LABEL_BACKGROUND = QColor(222, 225, 223, 145)
LABEL_BORDER = QColor(150, 155, 152, 115)
LABEL_TEXT = QColor(52, 56, 54, 200)
LABEL_TEXT_STALE = QColor(96, 100, 98, 200)


# ----------------------------------------------------------------------------- data
@dataclass(slots=True)
class MapMarker:
    key: str
    latitude: float
    longitude: float
    label: str = ""
    icon_path: str = ""
    overlay: str = ""
    stale: bool = False
    tooltip: str = ""
    payload: Any = None
    priority: int = 1
    clusterable: bool = True
    own: bool = False


@dataclass(slots=True)
class MapPolyline:
    points: list[tuple[float, float]]
    color: QColor
    width: float = 2.0
    halo: QColor | None = None
    halo_width: float = 0.0
    dashed: bool = False
    dots: bool = False
    dot_radius: float = 4.0
    tooltip: str = ""
    payload: Any = None
    opacity: float = 1.0
    point_tooltips: list[str] | None = None  # one per point: hovering a dot shows it
    point_payloads: list[Any] | None = None  # one per point: sent on a right click
    arrows: bool = False  # direction arrow in the middle of every segment
    arrow_at: float = 0.5  # where on a segment the arrow sits (0.5 = the middle; more = nearer its end)


@dataclass(slots=True)
class MapPolygon:
    rings: list[list[tuple[float, float]]]
    stroke: QColor
    fill: QColor
    width: float = 1.5
    stroke_opacity: float = 1.0
    fill_opacity: float = 0.1
    tooltip: str = ""
    payload: Any = None


@dataclass(slots=True)
class _Cluster:
    members: list[MapMarker]
    world_x: float
    world_y: float

    @property
    def stale(self) -> bool:
        return bool(self.members) and all(member.stale for member in self.members)


@dataclass
class _RulerState:
    a: tuple[float, float] = (0.0, 0.0)
    b: tuple[float, float] = (0.0, 0.0)
    active: bool = False  # True once the user dragged a handle


# ------------------------------------------------------------------------------ view
class MapView(QWidget):
    view_changed = Signal(float, float, int)
    marker_clicked = Signal(object)
    marker_double_clicked = Signal(object)
    marker_hovered = Signal(object)
    # Payloads of the track dots under a right click; an empty list = right click elsewhere or Esc.
    track_points_selected = Signal(object, QPoint)
    marker_right_clicked = Signal(object, QPoint)  # a station under a right click (before the dots)
    map_clicked = Signal(float, float)
    tile_status_changed = Signal(str)
    _views: "weakref.WeakSet[MapView]" = weakref.WeakSet()  # every map, to know when none is on screen

    LAYER_ORDER = ("areas", "coverage", "tracks", "paths", "reception", "highlight")
    # "Super zoom" (the user's choice, as on the user's CWOP weather map): the map zooms in further
    # than the tiles exist; past the source's own max zoom its last tiles are enlarged.
    MAX_ZOOM = 25
    TOP_LAYERS = ("reception", "highlight")  # what the user picked: drawn above the grid

    def __init__(self, parent: QWidget | None = None, *, interactive: bool = True,
                 tile_manager: TileManager | None = None, show_zoom_control: bool = True,
                 show_attribution: bool = True) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if interactive else Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 160)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.interactive = interactive
        self.tiles = tile_manager or shared_tile_manager()
        MapView._views.add(self)
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.setInterval(RELEASE_UNSEEN_MS)
        self._release_timer.timeout.connect(self._release_memory_if_unseen)
        self.tiles.tile_ready.connect(self.update)
        # Signal to signal (not .emit): Qt drops the connection with this map. The tile manager outlives
        # maps rebuilt with their pages; a connection to .emit stayed and raised "Signal source has been
        # deleted" (the error window) at the next tile status change.
        self.tiles.status_changed.connect(self.tile_status_changed)
        self.tiles.source_changed.connect(self._apply_tile_source)
        self.center_lat = 52.0
        self.center_lon = 19.0
        self.zoom = 6
        self.min_zoom = max(0, self.tiles.source.min_zoom if self.tiles.source.valid else 0)
        self.native_max_zoom = min(22, self.tiles.source.max_zoom if self.tiles.source.valid else 19)
        self.max_zoom = max(self.MAX_ZOOM, self.native_max_zoom)
        self.markers: list[MapMarker] = []
        self.layers: dict[str, list[MapPolyline | MapPolygon]] = {name: [] for name in self.LAYER_ORDER}
        self.layer_visible: dict[str, bool] = {name: True for name in self.LAYER_ORDER}
        self.labels_visible = True
        self.modern_symbols = False
        self.cluster_enabled = False
        self.cluster_radius_px = 28
        self.spiderfy_enabled = False
        self.spiderfy_levels_before_max = 2
        self.spiderfy_nearby_px = 20
        self.grid_visible = False
        self.ruler_visible = False
        self.mask_color = QColor(0, 0, 0)
        self.mask_opacity = 0.0
        self.reference_point: tuple[float, float] | None = None
        self._ruler = _RulerState()
        self._ruler_drag: str | None = None
        self._drag_start: QPoint | None = None
        self._drag_center_px: tuple[float, float] | None = None
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._clusters: list[_Cluster] = []
        self._clusters_dirty = True
        self._info_text = ""  # the station info window a left click opened
        self._skip_release = False  # the release that ends a double click
        self._spider: dict[str, Any] | None = None
        self._spider_collapse_timer = QTimer(self)
        self._spider_collapse_timer.setSingleShot(True)
        self._spider_collapse_timer.timeout.connect(self._collapse_spider)
        self._label_cache: OrderedDict[tuple[Any, ...], QPixmap] = OrderedDict()
        self._last_paint_ms = 0.0
        self.zoom_in_button: QToolButton | None = None
        self.zoom_out_button: QToolButton | None = None
        self.attribution: QLabel | None = None
        if show_zoom_control and interactive:
            self._build_zoom_control()
        if show_attribution:
            self.attribution = QLabel(self)
            self.attribution.setObjectName("MapAttribution")
            self.attribution.setStyleSheet(
                "QLabel#MapAttribution { background: rgba(255,255,255,0.78); color: #333; font-size: 8pt; padding: 1px 5px;"
                " border-top-left-radius: 4px; }"
            )
            self.attribution.setText(self.tiles.source.attribution)
            self.attribution.adjustSize()

    # ------------------------------------------------------------------ public API
    def set_view(self, latitude: float, longitude: float, zoom: int | None = None, *, emit: bool = True) -> None:
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            return
        self.center_lat = geo.clamp(float(latitude), -geo.MAX_LATITUDE, geo.MAX_LATITUDE)
        self.center_lon = geo.normalize_longitude(float(longitude))
        if zoom is not None:
            self.zoom = int(geo.clamp(int(zoom), self.min_zoom, self.max_zoom))
        self._clusters_dirty = True
        self._collapse_spider()
        self._anchor_ruler_if_idle()
        self.update()
        if emit:
            self.view_changed.emit(self.center_lat, self.center_lon, self.zoom)

    def center_on(self, latitude: float, longitude: float, min_zoom: int = 15, *, anchor: QPointF | None = None) -> None:
        self.set_view_at(latitude, longitude, max(self.zoom, min(min_zoom, self.max_zoom)), anchor=anchor)

    def set_view_at(self, latitude: float, longitude: float, zoom: int | None = None, *,
                    anchor: QPointF | None = None) -> None:
        """Like set_view, but the point lands at ``anchor`` (widget coordinates) instead of the middle.

        The map page passes the middle of the part its overlay panels leave uncovered.
        """
        if anchor is None or not (math.isfinite(latitude) and math.isfinite(longitude)):
            self.set_view(latitude, longitude, zoom)
            return
        target_zoom = self.zoom if zoom is None else int(geo.clamp(int(zoom), self.min_zoom, self.max_zoom))
        x, y = geo.project(latitude, longitude, target_zoom)
        center_lat, center_lon = geo.unproject(x + self.width() / 2 - anchor.x(), y + self.height() / 2 - anchor.y(),
                                               target_zoom)
        self.set_view(center_lat, center_lon, target_zoom)

    def fit_points(self, points: Iterable[tuple[float, float]], *, padding: int = 24, max_zoom: int = 14) -> None:
        bounds = geo.bounds_of(points)
        if bounds is None:
            return
        south, west, north, east = bounds
        zoom = geo.fit_zoom(bounds, max(1, self.width()), max(1, self.height()), padding=padding,
                            min_zoom=self.min_zoom, max_zoom=min(max_zoom, self.max_zoom))
        self.set_view((south + north) / 2, (west + east) / 2, zoom)

    def reload_tile_source(self) -> None:
        self.tiles.reload_source()
        self._apply_tile_source()

    def _apply_tile_source(self) -> None:
        source = self.tiles.source
        if source.valid:
            self.min_zoom = max(0, source.min_zoom)
            self.native_max_zoom = min(22, source.max_zoom)
            self.max_zoom = max(self.MAX_ZOOM, self.native_max_zoom)
            self.zoom = int(geo.clamp(self.zoom, self.min_zoom, self.max_zoom))
            self._clusters_dirty = True
        if self.attribution is not None:
            self.attribution.setText(source.attribution)
            self.attribution.adjustSize()
            self._position_overlays()
        self.update()

    def set_markers(self, markers: list[MapMarker]) -> None:
        self.markers = list(markers)
        self._clusters_dirty = True
        if self._spider is not None:
            keys = {marker.key for marker in self.markers}
            if any(member.key not in keys for member in self._spider["members"]):
                self._collapse_spider()
        self.update()

    def set_layer(self, name: str, shapes: list[MapPolyline | MapPolygon]) -> None:
        self.layers[name] = list(shapes)
        self.update()

    def set_layer_visible(self, name: str, visible: bool) -> None:
        self.layer_visible[name] = bool(visible)
        self.update()

    def set_grid_visible(self, visible: bool) -> None:
        self.grid_visible = bool(visible)
        self.update()

    def set_ruler_visible(self, visible: bool) -> None:
        self.ruler_visible = bool(visible)
        if visible and not self._ruler.active:
            self._anchor_ruler_if_idle(force=True)
        self.update()

    def set_mask(self, color: QColor, opacity: float) -> None:
        self.mask_color = QColor(color)
        self.mask_opacity = geo.clamp(float(opacity), 0.0, 1.0)
        self.update()

    def set_clustering(self, enabled: bool, *, spiderfy: bool = False, levels_before_max: int = 2,
                       nearby_px: int = 20) -> None:
        self.cluster_enabled = bool(enabled)
        self.spiderfy_enabled = bool(spiderfy)
        self.spiderfy_levels_before_max = int(geo.clamp(levels_before_max, 0, 10))
        self.spiderfy_nearby_px = int(geo.clamp(nearby_px, 1, 100))
        self._clusters_dirty = True
        self.update()

    def set_modern_symbols(self, modern: bool) -> None:
        self.modern_symbols = bool(modern)
        self._label_cache.clear()
        self._clusters_dirty = True
        self.update()

    def theme_changed(self) -> None:
        self._label_cache.clear()
        self.update()

    def lat_lon_at(self, point: QPoint | QPointF) -> tuple[float, float]:
        origin_x, origin_y = self._origin()
        latitude, longitude = geo.unproject(origin_x + point.x(), origin_y + point.y(), self.zoom)
        return latitude, geo.normalize_longitude(longitude)

    def screen_point(self, latitude: float, longitude: float) -> QPointF:
        x, y = self._world_point(latitude, longitude)
        origin_x, origin_y = self._origin()
        return QPointF(x - origin_x, y - origin_y)

    def visible_radius_km(self) -> float:
        radius_px = max(1, min(self.width(), self.height()) // 2)
        center = QPointF(self.width() / 2, self.height() / 2)
        edge_lat, edge_lon = self.lat_lon_at(QPointF(center.x(), max(0.0, center.y() - radius_px)))
        return geo.haversine_m(self.center_lat, self.center_lon, edge_lat, edge_lon) / 1000

    def ruler_measurement(self) -> dict[str, float]:
        (lat_a, lon_a), (lat_b, lon_b) = self._ruler.a, self._ruler.b
        return {
            "distance_m": geo.haversine_m(lat_a, lon_a, lat_b, lon_b),
            "bearing_ab": geo.bearing_deg(lat_a, lon_a, lat_b, lon_b),
            "bearing_ba": geo.bearing_deg(lat_b, lon_b, lat_a, lon_a),
        }

    # -------------------------------------------------------------- coordinates
    def _center_world(self) -> tuple[float, float]:
        return geo.project(self.center_lat, self.center_lon, self.zoom)

    def _origin(self) -> tuple[float, float]:
        cx, cy = self._center_world()
        return cx - self.width() / 2, cy - self.height() / 2

    def _world_point(self, latitude: float, longitude: float) -> tuple[float, float]:
        x, y = geo.project(latitude, longitude, self.zoom)
        world = geo.world_size(self.zoom)
        cx, _ = self._center_world()
        dx = x - cx
        if abs(dx) > world / 2:
            x -= round(dx / world) * world
        return x, y

    # ------------------------------------------------------------------- overlays
    def _build_zoom_control(self) -> None:
        self.zoom_in_button = QToolButton(self)
        self.zoom_out_button = QToolButton(self)
        for button, text, delta in ((self.zoom_in_button, "+", 1), (self.zoom_out_button, "−", -1)):
            button.setText(text)
            button.setFixedSize(30, 30)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QToolButton { background: rgba(255,255,255,0.95); color: #222; border: 1px solid rgba(0,0,0,0.25);"
                " border-radius: 4px; font-size: 15pt; font-weight: 700; padding: 0px; }"
                "QToolButton:hover { background: #f0f0f0; }"
            )
            button.clicked.connect(lambda _checked=False, step=delta: self.zoom_by(step))
        self._position_overlays()

    def zoom_by(self, step: int, anchor: QPointF | None = None) -> None:
        new_zoom = int(geo.clamp(self.zoom + step, self.min_zoom, self.max_zoom))
        if new_zoom == self.zoom:
            return
        if anchor is None:
            self.set_view(self.center_lat, self.center_lon, new_zoom)
            return
        latitude, longitude = self.lat_lon_at(anchor)
        ax, ay = geo.project(latitude, longitude, new_zoom)
        cx = ax - (anchor.x() - self.width() / 2)
        cy = ay - (anchor.y() - self.height() / 2)
        center_lat, center_lon = geo.unproject(cx, cy, new_zoom)
        self.zoom = new_zoom
        self.set_view(center_lat, center_lon, new_zoom)

    def _position_overlays(self) -> None:
        if self.zoom_in_button is not None and self.zoom_out_button is not None:
            self.zoom_in_button.move(10, 10)
            self.zoom_out_button.move(10, 41)
        if self.attribution is not None:
            self.attribution.adjustSize()
            self.attribution.move(self.width() - self.attribution.width(), self.height() - self.attribution.height())
            self.attribution.setVisible(bool(self.attribution.text()))

    def zoom_control_bottom(self) -> int:
        if self.zoom_out_button is not None:
            return self.zoom_out_button.geometry().bottom()
        return 0

    # ----------------------------------------------------------------- clustering
    def spiderfy_activation_zoom(self) -> float:
        if not self.spiderfy_enabled:
            return math.inf
        return max(self.min_zoom, self.native_max_zoom - self.spiderfy_levels_before_max)

    def _clustering_active(self) -> bool:
        if not self.cluster_enabled:
            return False
        return self.zoom < min(self.native_max_zoom, self.spiderfy_activation_zoom())

    def _rebuild_clusters(self) -> None:
        self._clusters_dirty = False
        clusters: list[_Cluster] = []
        if not self._clustering_active():
            self._clusters = [_Cluster([marker], *geo.project(marker.latitude, marker.longitude, self.zoom)) for marker in self.markers]
            return
        radius = float(self.cluster_radius_px)
        grid: dict[tuple[int, int], list[_Cluster]] = {}
        ordered = sorted(self.markers, key=lambda marker: (marker.priority, marker.key))
        for marker in ordered:
            x, y = geo.project(marker.latitude, marker.longitude, self.zoom)
            if not marker.clusterable:
                clusters.append(_Cluster([marker], x, y))
                continue
            cell = (int(x // radius), int(y // radius))
            found: _Cluster | None = None
            best = radius
            for gx in (cell[0] - 1, cell[0], cell[0] + 1):
                for gy in (cell[1] - 1, cell[1], cell[1] + 1):
                    for cluster in grid.get((gx, gy), ()):
                        distance = math.hypot(cluster.world_x - x, cluster.world_y - y)
                        if distance <= best:
                            best = distance
                            found = cluster
            if found is None:
                found = _Cluster([marker], x, y)
                clusters.append(found)
                grid.setdefault(cell, []).append(found)
            else:
                found.members.append(marker)
        self._clusters = clusters

    # -------------------------------------------------------------------- painting
    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        started = time.perf_counter()
        colors = current_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), colors.color("panel-alt"))
        self._paint_tiles(painter)
        if self.mask_opacity > 0:
            mask = QColor(self.mask_color)
            mask.setAlphaF(self.mask_opacity)
            painter.fillRect(self.rect(), mask)
        origin = self._origin()
        for name in self.LAYER_ORDER:
            if name in self.TOP_LAYERS:
                continue
            if not self.layer_visible.get(name, True):
                continue
            for shape in self.layers.get(name, ()):
                if isinstance(shape, MapPolygon):
                    self._paint_polygon(painter, shape, origin)
                else:
                    self._paint_polyline(painter, shape, origin)
        if self.grid_visible:
            self._paint_grid(painter, colors)
        for shape in (shape for name in self.TOP_LAYERS for shape in self.layers.get(name, ())):
            if isinstance(shape, MapPolygon):
                self._paint_polygon(painter, shape, origin)
            else:
                self._paint_polyline(painter, shape, origin)
        if self._clusters_dirty:
            self._rebuild_clusters()
        self._paint_markers(painter, origin, colors)
        if self._spider is not None:
            self._paint_spider(painter, origin, colors)
        if self.ruler_visible:
            self._paint_ruler(painter, origin, colors)
        painter.end()
        self._last_paint_ms = (time.perf_counter() - started) * 1000

    def _paint_tiles(self, painter: QPainter) -> None:
        if not self.tiles.source.valid:
            return
        # Whole pixels (the user saw lines between the tiles): a tile edge at a fraction of a pixel was
        # anti-aliased, so the background showed through the seam. The shapes on top keep the exact origin.
        origin_x, origin_y = (round(value) for value in self._origin())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # Past the source's max zoom the tiles of that zoom are enlarged (super zoom): nothing more
        # is downloaded, the symbols and lines on top stay sharp.
        tile_zoom = min(self.zoom, self.native_max_zoom)
        tile = geo.TILE_SIZE * (1 << (self.zoom - tile_zoom))
        count = 1 << tile_zoom
        first_x = int(math.floor(origin_x / tile))
        last_x = int(math.floor((origin_x + self.width()) / tile))
        first_y = max(0, int(math.floor(origin_y / tile)))
        last_y = min(count - 1, int(math.floor((origin_y + self.height()) / tile)))
        center_tx = (origin_x + self.width() / 2) / tile
        center_ty = (origin_y + self.height() / 2) / tile
        requests: list[tuple[float, int, int, int]] = []
        for ty in range(first_y, last_y + 1):
            for tx in range(first_x, last_x + 1):
                wrapped = tx % count
                target = QRectF(tx * tile - origin_x, ty * tile - origin_y, tile, tile)
                pixmap = self.tiles.cached(tile_zoom, wrapped, ty)
                if pixmap is not None:
                    self._draw_tile(painter, target, pixmap, QRectF(pixmap.rect()))
                    continue
                requests.append(((tx + 0.5 - center_tx) ** 2 + (ty + 0.5 - center_ty) ** 2, wrapped, ty, tx))
                self._paint_parent_tile(painter, target, tile_zoom, wrapped, ty)
        painter.restore()
        for _distance, wrapped, ty, _tx in sorted(requests):
            self.tiles.request(tile_zoom, wrapped, ty)

    def _draw_tile(self, painter: QPainter, target: QRectF, pixmap: QPixmap, source: QRectF) -> None:
        """Draw only the visible part: an enlarged tile can be thousands of pixels wide."""
        visible = target.intersected(QRectF(self.rect()))
        if visible.isEmpty():
            return
        scale = source.width() / target.width()
        part = QRectF(source.x() + (visible.x() - target.x()) * scale, source.y() + (visible.y() - target.y()) * scale,
                      visible.width() * scale, visible.height() * scale)
        painter.drawPixmap(visible, pixmap, part)

    def _paint_parent_tile(self, painter: QPainter, target: QRectF, zoom: int, x: int, y: int) -> None:
        for levels in range(1, 5):
            parent_zoom = zoom - levels
            if parent_zoom < 0:
                return
            scale = 1 << levels
            pixmap = self.tiles.cached(parent_zoom, x // scale, y // scale)
            if pixmap is None:
                continue
            size = pixmap.width() / scale
            source = QRectF((x % scale) * size, (y % scale) * size, size, size)
            self._draw_tile(painter, target, pixmap, source)
            return

    def _path_for(self, points: list[tuple[float, float]], origin: tuple[float, float]) -> QPainterPath:
        path = QPainterPath()
        first = True
        for latitude, longitude in points:
            x, y = self._world_point(latitude, longitude)
            point = QPointF(x - origin[0], y - origin[1])
            if first:
                path.moveTo(point)
                first = False
            else:
                path.lineTo(point)
        return path

    def _paint_polyline(self, painter: QPainter, shape: MapPolyline, origin: tuple[float, float]) -> None:
        if len(shape.points) < 2:
            return
        path = self._path_for(shape.points, origin)
        if not path.boundingRect().adjusted(-20, -20, 20, 20).intersects(QRectF(self.rect())):
            return
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if shape.halo is not None and shape.halo_width > 0:
            halo_pen = QPen(shape.halo, shape.halo_width)
            halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            halo_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(halo_pen)
            painter.drawPath(path)
        color = QColor(shape.color)
        color.setAlphaF(color.alphaF() * shape.opacity)
        pen = QPen(color, shape.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if shape.dashed:
            pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.drawPath(path)
        if shape.dots:
            painter.setPen(QPen(shape.halo or QColor("#ffffff"), 2))
            painter.setBrush(shape.color)
            for latitude, longitude in shape.points[:-1]:
                x, y = self._world_point(latitude, longitude)
                painter.drawEllipse(QPointF(x - origin[0], y - origin[1]), shape.dot_radius, shape.dot_radius)
        if shape.arrows:
            self._paint_arrows(painter, shape, origin, color)
        painter.restore()

    def _paint_arrows(self, painter: QPainter, shape: MapPolyline, origin: tuple[float, float], color: QColor) -> None:
        size = 4.0 + shape.width * 1.5
        painter.setPen(QPen(shape.halo, 1.5) if shape.halo is not None else Qt.PenStyle.NoPen)
        painter.setBrush(color)
        previous: QPointF | None = None
        for latitude, longitude in shape.points:
            x, y = self._world_point(latitude, longitude)
            current = QPointF(x - origin[0], y - origin[1])
            if previous is not None:
                dx, dy = current.x() - previous.x(), current.y() - previous.y()
                length = math.hypot(dx, dy)
                if length >= size * 4:  # no arrow on a segment shorter than a few arrows
                    ux, uy = dx / length, dy / length
                    tip = QPointF(previous.x() + dx * shape.arrow_at + ux * size, previous.y() + dy * shape.arrow_at + uy * size)
                    back = QPointF(tip.x() - ux * size * 2, tip.y() - uy * size * 2)
                    painter.drawPolygon(QPolygonF([
                        tip,
                        QPointF(back.x() - uy * size, back.y() + ux * size),
                        QPointF(back.x() + uy * size, back.y() - ux * size),
                    ]))
            previous = current

    def _paint_polygon(self, painter: QPainter, shape: MapPolygon, origin: tuple[float, float]) -> None:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for ring in shape.rings:
            if len(ring) < 3:
                continue
            path.addPath(self._path_for(list(ring) + [ring[0]], origin))
        if path.isEmpty():
            return
        if not path.boundingRect().adjusted(-4, -4, 4, 4).intersects(QRectF(self.rect())):
            return
        painter.save()
        fill = QColor(shape.fill)
        fill.setAlphaF(geo.clamp(shape.fill_opacity, 0.0, 1.0))
        stroke = QColor(shape.stroke)
        stroke.setAlphaF(geo.clamp(shape.stroke_opacity, 0.0, 1.0))
        painter.setBrush(fill)
        painter.setPen(QPen(stroke, shape.width) if shape.width > 0 else Qt.PenStyle.NoPen)
        painter.drawPath(path)
        painter.restore()

    def _paint_grid(self, painter: QPainter, colors: Any) -> None:
        spec = geo.maidenhead_grid_spec(self.zoom)
        lon_step = float(spec["lon_step"])
        lat_step = float(spec["lat_step"])
        precision = int(spec["precision"])
        north, west = self.lat_lon_at(QPointF(0, 0))
        south, east = self.lat_lon_at(QPointF(self.width(), self.height()))
        origin_x, _ = self._origin()
        # Longitudes continuous with the view (no wrapping for grid math).
        west = geo.unproject(origin_x, 0, self.zoom)[1]
        east = geo.unproject(origin_x + self.width(), 0, self.zoom)[1]
        south = max(-90.0, south)
        north = min(90.0, north)
        first_lon = math.floor((west + 180) / lon_step)
        last_lon = math.ceil((east + 180) / lon_step) - 1
        first_lat = max(0, math.floor((south + 90) / lat_step))
        last_lat = min(round(180 / lat_step) - 1, math.ceil((north + 90) / lat_step) - 1)
        if (last_lon - first_lon) > 400 or (last_lat - first_lat) > 400:
            return
        dark = colors.is_dark
        line_color = QColor("#d5dde7" if dark else "#334155")
        line_color.setAlphaF(0.68 if dark else 0.72)
        label_color = QColor("#f1f5f9" if dark else "#172033")
        label_color.setAlphaF(0.82)
        halo_color = QColor(5, 9, 7, 240) if dark else QColor(255, 255, 255, 245)
        painter.save()
        painter.setPen(QPen(line_color, 1.8 if precision == 2 else 1.35))
        origin = self._origin()
        top = self._grid_y(min(90.0, -90 + (last_lat + 1) * lat_step), origin)
        bottom = self._grid_y(-90 + first_lat * lat_step, origin)
        for index in range(first_lon, last_lon + 2):
            longitude = -180 + index * lon_step
            x = geo.project(0, longitude, self.zoom)[0] - origin[0]
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        left = geo.project(0, -180 + first_lon * lon_step, self.zoom)[0] - origin[0]
        right = geo.project(0, -180 + (last_lon + 1) * lon_step, self.zoom)[0] - origin[0]
        for index in range(first_lat, last_lat + 2):
            latitude = -90 + index * lat_step
            y = self._grid_y(latitude, origin)
            painter.drawLine(QPointF(left, y), QPointF(right, y))
        cell_width = abs(geo.project(0, lon_step, self.zoom)[0] - geo.project(0, 0, self.zoom)[0])
        max_font = {2: 54.0, 4: 20.0 if self.zoom == 6 else 38.0, 6: 18.0 if self.zoom == 11 else 30.0}.get(precision, 24.0)
        width_limited = cell_width / (precision * 0.72)
        for lat_index in range(first_lat, last_lat + 1):
            cell_south = -90 + lat_index * lat_step
            cell_north = cell_south + lat_step
            y_top = self._grid_y(cell_north, origin)
            y_bottom = self._grid_y(cell_south, origin)
            font_px = max(6.0, min(max_font, abs(y_bottom - y_top) * 0.42, width_limited))
            font = QFont(MONO_FONT_FAMILY)
            font.setPixelSize(int(font_px))
            font.setBold(True)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
            painter.setFont(font)
            for lon_index in range(first_lon, last_lon + 1):
                center_lon = -180 + (lon_index + 0.5) * lon_step
                x = geo.project(0, center_lon, self.zoom)[0] - origin[0]
                locator = geo.maidenhead_locator_for_cell(lon_index, lat_index, precision)
                rect = QRectF(x - 200, (y_top + y_bottom) / 2 - font_px, 400, font_px * 2)
                painter.setPen(halo_color)
                for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    painter.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, locator)
                painter.setPen(label_color)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, locator)
        painter.restore()

    def _grid_y(self, latitude: float, origin: tuple[float, float]) -> float:
        return geo.project(geo.clamp(latitude, -geo.MAX_LATITUDE, geo.MAX_LATITUDE), 0, self.zoom)[1] - origin[1]

    @staticmethod
    def _label_font() -> QFont:
        font = QFont(UI_FONT_FAMILY)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _label_height(self) -> int:
        """Height of the callsign bubble (without its 1 px border)."""
        if getattr(self, "_label_height_px", None) is None:
            self._label_height_px = QFontMetrics(self._label_font()).height() + 5
        return self._label_height_px

    def _icon_size(self) -> int:
        if self.modern_symbols:
            return MODERN_ICON_PX
        # The classic drawing (about 16 of its 22 px) is as tall as the callsign bubble next to it.
        return round(self._label_height() * LEGACY_SYMBOL_FILE_PX / LEGACY_SYMBOL_ART_PX)

    def _symbol_half_width(self, size: int) -> float:
        return size / 2 if self.modern_symbols else size * LEGACY_SYMBOL_ART_PX / LEGACY_SYMBOL_FILE_PX / 2

    def _label_origin(self, point: QPointF, label: QPixmap, size: int) -> QPointF:
        """Bubble to the right of the symbol, centred on it vertically (painting and hit-testing)."""
        height = label.height() / label.devicePixelRatio()
        return QPointF(point.x() + self._symbol_half_width(size) + 3, point.y() - height / 2)

    def _label_pixmap(self, text: str, stale: bool, own: bool) -> QPixmap:
        colors = current_colors()
        key = (text, stale, own, colors.palette_name, colors.mode)
        cached = self._label_cache.get(key)
        if cached is not None:
            self._label_cache.move_to_end(key)
            return cached
        font = self._label_font()
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 5
        ratio = 2.0
        image = QImage(int((width + 2) * ratio), int((height + 2) * ratio), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.scale(ratio, ratio)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = LABEL_TEXT_STALE if stale else LABEL_TEXT
        border = colors.color("accent") if own else LABEL_BORDER
        painter.setPen(QPen(border, 1))
        painter.setBrush(LABEL_BACKGROUND)
        painter.drawRoundedRect(QRectF(0.5, 0.5, width, height), height / 2, height / 2)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(QRectF(0.5, 0.5, width, height), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(ratio)
        self._label_cache[key] = pixmap
        while len(self._label_cache) > LABEL_CACHE_MAX:
            self._label_cache.popitem(last=False)  # the label used longest ago
        return pixmap

    def _marker_screen(self, marker: MapMarker, origin: tuple[float, float]) -> QPointF:
        x, y = self._world_point(marker.latitude, marker.longitude)
        return QPointF(x - origin[0], y - origin[1])

    def _paint_marker(self, painter: QPainter, marker: MapMarker, point: QPointF, colors: Any) -> None:
        size = self._icon_size()
        pixmap = symbol_pixmap(marker.icon_path, marker.overlay, size)
        painter.save()
        if marker.stale:
            painter.setOpacity(0.58)
        if self.modern_symbols:
            glow = QColor(255, 255, 255, 200) if colors.is_dark else QColor(0, 0, 0, 200)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(glow.red(), glow.green(), glow.blue(), 40))
            painter.drawEllipse(point, size / 2 + 1, size / 2 + 1)
        else:
            # Classic symbols are drawn without their white tile; a soft shadow keeps them readable.
            shadow = symbol_shadow_pixmap(marker.icon_path, marker.overlay, size)
            painter.drawPixmap(QPointF(point.x() - size / 2, point.y() - size / 2 + 1), shadow)
        painter.drawPixmap(QPointF(point.x() - size / 2, point.y() - size / 2), pixmap)
        painter.restore()
        if self.labels_visible and marker.label:
            label = self._label_pixmap(marker.label, marker.stale, marker.own)
            painter.drawPixmap(self._label_origin(point, label, size), label)

    def _paint_cluster(self, painter: QPainter, cluster: _Cluster, point: QPointF, colors: Any) -> None:
        painter.save()
        radius = 15.0
        border = QColor(240, 245, 241, 235) if colors.is_dark else QColor(30, 35, 40, 190)
        fill = QColor(96, 108, 116, 224) if cluster.stale else QColor(24, 108, 200, 235)
        painter.setPen(QPen(QColor(0, 0, 0, 70), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, radius + 1, radius + 1)
        painter.setPen(QPen(border, 2))
        painter.setBrush(fill)
        painter.drawEllipse(point, radius, radius)
        font = QFont(UI_FONT_FAMILY)
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#f5f8fb"))
        painter.drawText(QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2),
                         Qt.AlignmentFlag.AlignCenter, str(len(cluster.members)))
        painter.restore()

    def _paint_markers(self, painter: QPainter, origin: tuple[float, float], colors: Any) -> None:
        viewport = QRectF(self.rect()).adjusted(-40, -40, 140, 40)
        spider_keys = {member.key for member in self._spider["members"]} if self._spider else set()
        # Non-local markers first so the own station stays on top.
        ordered = sorted(self._clusters, key=lambda cluster: (any(member.own for member in cluster.members), -max(member.priority for member in cluster.members)))
        for cluster in ordered:
            x, y = cluster.world_x, cluster.world_y
            world = geo.world_size(self.zoom)
            cx, _ = self._center_world()
            if abs(x - cx) > world / 2:
                x -= round((x - cx) / world) * world
            point = QPointF(x - origin[0], y - origin[1])
            if not viewport.contains(point):
                continue
            if len(cluster.members) > 1:
                self._paint_cluster(painter, cluster, point, colors)
                continue
            marker = cluster.members[0]
            if marker.key in spider_keys:
                continue
            self._paint_marker(painter, marker, point, colors)

    # ------------------------------------------------------------------- spiderfy
    def _nearby_markers(self, marker: MapMarker) -> list[MapMarker]:
        origin = self._origin()
        anchor = self._marker_screen(marker, origin)
        limit = float(self.spiderfy_nearby_px)
        result = []
        for candidate in self.markers:
            point = self._marker_screen(candidate, origin)
            if math.hypot(point.x() - anchor.x(), point.y() - anchor.y()) < limit:
                result.append(candidate)
        return result

    def _open_spider(self, members: list[MapMarker], center: QPointF) -> None:
        if len(members) < 2:
            return
        count = len(members)
        size = self._icon_size()
        # Spacing tuned for 32 px modern / 20 px classic icons; it grows with the icon.
        scale = size / (MODERN_ICON_PX if self.modern_symbols else 20)
        positions: list[QPointF] = []
        if count <= 8:
            separation = (42 if self.modern_symbols else 32) * scale
            radius = separation * (2 + count) / (2 * math.pi)
            for index in range(count):
                angle = 2 * math.pi * index / count - math.pi / 2
                positions.append(QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle)))
        else:
            leg_length = (16.0 if self.modern_symbols else 12.0) * scale
            separation = (44.0 if self.modern_symbols else 34.0) * scale
            factor = (7.0 if self.modern_symbols else 5.5) * scale
            angle = 0.0
            for _index in range(count):
                angle += separation / leg_length + _index * 0.0005
                positions.append(QPointF(center.x() + leg_length * math.cos(angle), center.y() + leg_length * math.sin(angle)))
                leg_length += 2 * math.pi * factor / angle
        latlon_center = self.lat_lon_at(center)
        self._spider = {
            "members": list(members),
            "positions": positions,
            "center": latlon_center,
            "zoom": self.zoom,
            "bounds": QRectF(center, center).united(QRectF(*self._bounds_of_points(positions))).adjusted(-size - 28, -size - 28, size + 28, size + 28),
        }
        self.update()

    @staticmethod
    def _bounds_of_points(points: list[QPointF]) -> tuple[float, float, float, float]:
        xs = [point.x() for point in points]
        ys = [point.y() for point in points]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    def _collapse_spider(self) -> None:
        if self._spider is not None:
            self._spider = None
            self.update()

    def _paint_spider(self, painter: QPainter, origin: tuple[float, float], colors: Any) -> None:
        spider = self._spider
        if spider is None:
            return
        if spider["zoom"] != self.zoom:
            self._spider = None
            return
        center_lat, center_lon = spider["center"]
        center = self.screen_point(center_lat, center_lon)
        painter.save()
        painter.setPen(QPen(QColor("#7a8a94"), 1.5))
        for position in spider["positions"]:
            painter.drawLine(center, position)
        painter.restore()
        for marker, position in zip(spider["members"], spider["positions"]):
            self._paint_marker(painter, marker, position, colors)

    # ---------------------------------------------------------------------- ruler
    def _anchor_ruler_if_idle(self, force: bool = False) -> None:
        if self._ruler.active and not force:
            return
        width, height = max(1, self.width()), max(1, self.height())
        anchor_y = max(40, height - 56)
        left_padding, right_padding = 16, 24
        viewport_right = max(left_padding + 48, width - right_padding)
        min_gap = max(48, min(90, viewport_right - left_padding))
        preferred_gap = max(min_gap, min(220, viewport_right - left_padding))
        first_x = max(left_padding, min(168, viewport_right - min_gap))
        second_x = min(first_x + preferred_gap, viewport_right)
        if second_x - first_x < min_gap:
            first_x = max(left_padding, second_x - min_gap)
        self._ruler.a = self.lat_lon_at(QPointF(first_x, anchor_y))
        self._ruler.b = self.lat_lon_at(QPointF(second_x, anchor_y))

    def _ruler_points(self) -> tuple[QPointF, QPointF]:
        return self.screen_point(*self._ruler.a), self.screen_point(*self._ruler.b)

    def _paint_ruler(self, painter: QPainter, origin: tuple[float, float], colors: Any) -> None:
        point_a, point_b = self._ruler_points()
        painter.save()
        halo = QPen(QColor(255, 255, 255, 250), 8)
        halo.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(halo)
        painter.drawLine(point_a, point_b)
        core = QPen(QColor("#0078ff"), 3.4)
        core.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(core)
        painter.drawLine(point_a, point_b)
        measurement = self.ruler_measurement()
        distance_km = measurement["distance_m"] / 1000
        distance_text = f"{distance_km:.{2 if distance_km < 10 else 1}f} km"
        from app.desktop.i18n import t

        for side, point, bearing, offset in (
            ("A", point_a, measurement["bearing_ab"], -28),
            ("B", point_b, measurement["bearing_ba"], 28),
        ):
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QColor("#0078ff") if side == "A" else QColor("#e8590c"))
            painter.drawEllipse(point, 8, 8)
            font = QFont(UI_FONT_FAMILY)
            font.setPixelSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(point.x() - 8, point.y() - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, side)
            title = "A -> B" if side == "A" else "B -> A"
            lines = [title, f"{t('Course')}: {round(bearing) % 360}°", f"{t('Distance')}: {distance_text}"]
            self._paint_tooltip_box(painter, QPointF(point.x() + offset, point.y() - 14), lines, colors, above=True)
        painter.restore()

    def _paint_tooltip_box(self, painter: QPainter, anchor: QPointF, lines: list[str], colors: Any, *,
                           above: bool = True) -> None:
        font = QFont(UI_FONT_FAMILY)
        font.setPixelSize(11)
        metrics = QFontMetrics(font)
        width = max(metrics.horizontalAdvance(line) for line in lines) + 14
        height = metrics.height() * len(lines) + 10
        rect = QRectF(anchor.x() - width / 2, anchor.y() - height if above else anchor.y(), width, height)
        painter.save()
        painter.setPen(QPen(colors.color("border"), 1))
        painter.setBrush(colors.color("panel-emphasis"))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(colors.color("text"))
        for index, line in enumerate(lines):
            line_font = QFont(font)
            line_font.setBold(index == 0)
            painter.setFont(line_font)
            painter.drawText(QRectF(rect.left() + 7, rect.top() + 5 + index * metrics.height(), width - 14, metrics.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.restore()

    # ------------------------------------------------------------------ hit tests
    def _hit_ruler(self, pos: QPointF) -> str | None:
        if not self.ruler_visible:
            return None
        point_a, point_b = self._ruler_points()
        if math.hypot(pos.x() - point_a.x(), pos.y() - point_a.y()) <= 11:
            return "a"
        if math.hypot(pos.x() - point_b.x(), pos.y() - point_b.y()) <= 11:
            return "b"
        return None

    def _spider_member_at(self, pos: QPointF) -> MapMarker | None:
        """The station on a leg of the open spider under this point."""
        if self._spider is None:
            return None
        size = self._icon_size()
        for marker, position in zip(self._spider["members"], self._spider["positions"]):
            if abs(pos.x() - position.x()) <= size / 2 + 2 and abs(pos.y() - position.y()) <= size / 2 + 2:
                return marker
        return None

    def _hit_marker(self, pos: QPointF) -> tuple[str, Any] | None:
        origin = self._origin()
        size = self._icon_size()
        leg = self._spider_member_at(pos)
        if leg is not None:
            return "marker", leg
        best: tuple[float, tuple[str, Any]] | None = None
        world = geo.world_size(self.zoom)
        cx, _ = self._center_world()
        for cluster in self._clusters:
            x, y = cluster.world_x, cluster.world_y
            if abs(x - cx) > world / 2:
                x -= round((x - cx) / world) * world
            point = QPointF(x - origin[0], y - origin[1])
            if len(cluster.members) > 1:
                distance = math.hypot(pos.x() - point.x(), pos.y() - point.y())
                if distance <= 16 and (best is None or distance < best[0]):
                    best = (distance, ("cluster", cluster))
                continue
            marker = cluster.members[0]
            dx, dy = pos.x() - point.x(), pos.y() - point.y()
            inside_icon = abs(dx) <= size / 2 + 2 and abs(dy) <= size / 2 + 2
            inside_label = False
            if self.labels_visible and marker.label:
                label = self._label_pixmap(marker.label, marker.stale, marker.own)
                label_rect = QRectF(self._label_origin(point, label, size),
                                    QSizeF(label.width() / label.devicePixelRatio(), label.height() / label.devicePixelRatio()))
                inside_label = label_rect.contains(pos)
            if inside_icon or inside_label:
                distance = math.hypot(dx, dy)
                if best is None or distance < best[0]:
                    best = (distance, ("marker", marker))
        return best[1] if best else None

    def _hit_points(self, pos: QPointF) -> list[tuple[MapPolyline, int]]:
        """Dots (with per-point details) under the cursor, nearest first; overlapping dots all count."""
        origin = self._origin()
        hits: list[tuple[float, MapPolyline, int]] = []
        for name in self.LAYER_ORDER:
            if not self.layer_visible.get(name, True):
                continue
            for shape in self.layers.get(name, ()):
                if not isinstance(shape, MapPolyline) or not shape.dots or not shape.point_tooltips:
                    continue
                reach = shape.dot_radius + 3
                for index, (latitude, longitude) in enumerate(shape.points):
                    x, y = self._world_point(latitude, longitude)
                    distance = math.hypot(pos.x() - (x - origin[0]), pos.y() - (y - origin[1]))
                    if distance <= reach and index < len(shape.point_tooltips):
                        hits.append((distance, shape, index))
        hits.sort(key=lambda hit: hit[0])
        return [(shape, index) for _distance, shape, index in hits]

    def _hit_shape(self, pos: QPointF) -> MapPolyline | MapPolygon | None:
        origin = self._origin()
        for name in ("reception", "paths", "tracks", "coverage", "areas"):
            if not self.layer_visible.get(name, True):
                continue
            for shape in reversed(self.layers.get(name, ())):
                if not getattr(shape, "tooltip", ""):
                    continue
                if isinstance(shape, MapPolyline):
                    previous: tuple[float, float] | None = None
                    for latitude, longitude in shape.points:
                        x, y = self._world_point(latitude, longitude)
                        current = (x - origin[0], y - origin[1])
                        if previous is not None and geo.point_segment_distance(pos.x(), pos.y(), *previous, *current) <= max(5.0, shape.width + 2):
                            return shape
                        previous = current
                elif isinstance(shape, MapPolygon):
                    for ring in shape.rings:
                        points = []
                        for latitude, longitude in ring:
                            x, y = self._world_point(latitude, longitude)
                            points.append((x - origin[0], y - origin[1]))
                        if geo.point_in_polygon(pos.x(), pos.y(), points):
                            return shape
        return None

    # --------------------------------------------------------------------- events
    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().hideEvent(event)
        self._release_timer.start()

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._release_timer.stop()

    @staticmethod
    def _on_screen(view: "MapView") -> bool:
        window = view.window()
        return view.isVisible() and window.isVisible() and not window.isMinimized()

    def _release_memory_if_unseen(self) -> None:
        if MapView._on_screen(self) or getattr(self.tiles, "fast", False):
            return  # "Fast map" keeps everything
        self._label_cache.clear()
        if not any(MapView._on_screen(view) for view in list(MapView._views) if view.tiles is self.tiles):
            self.tiles.clear_memory()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_overlays()
        self._anchor_ruler_if_idle()
        self._clusters_dirty = True

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt API
        pos = QPointF(event.pos())
        QToolTip.hideText()
        if self._clusters_dirty:
            self._rebuild_clusters()
        hit = self._hit_marker(pos)
        if hit is not None and hit[0] == "marker":  # the same thing the hover tooltip is about
            marker: MapMarker = hit[1]
            self.marker_right_clicked.emit(marker.payload if marker.payload is not None else marker, event.globalPos())
            event.accept()
            return
        payloads = [
            dot.point_payloads[index]
            for dot, index in self._hit_points(pos)
            if dot.point_payloads and index < len(dot.point_payloads)
        ]
        self.track_points_selected.emit(payloads, event.globalPos())
        event.setAccepted(bool(payloads))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        if not self.interactive:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.zoom_by(1 if delta > 0 else -1, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._skip_release = False  # a new click (the end of a double click may never have come)
        self._press_pos = event.position().toPoint()
        handle = self._hit_ruler(event.position())
        if handle is not None and self.interactive:
            self._ruler_drag = handle
            self._ruler.active = True
            return
        if self.interactive:
            self._drag_start = event.position().toPoint()
            self._drag_center_px = self._center_world()
            self._dragging = False
        self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        pos = event.position()
        if self._ruler_drag is not None:
            latlon = self.lat_lon_at(pos)
            if self._ruler_drag == "a":
                self._ruler.a = latlon
            else:
                self._ruler.b = latlon
            self.update()
            return
        if self._drag_start is not None and self._drag_center_px is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = pos.toPoint() - self._drag_start
            if not self._dragging and (abs(delta.x()) + abs(delta.y())) > 3:
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._collapse_spider()
                QToolTip.hideText()
            if self._dragging:
                cx = self._drag_center_px[0] - delta.x()
                cy = self._drag_center_px[1] - delta.y()
                latitude, longitude = geo.unproject(cx, cy, self.zoom)
                self.center_lat = geo.clamp(latitude, -geo.MAX_LATITUDE, geo.MAX_LATITUDE)
                self.center_lon = geo.normalize_longitude(longitude)
                self._anchor_ruler_if_idle()
                self.update()
            return
        self._update_hover(pos, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        if self._skip_release:  # the end of a double click is not another click
            self._skip_release = False
            return
        if self._ruler_drag is not None:
            self._ruler_drag = None
            self.update()
            return
        was_dragging = self._dragging
        self._drag_start = None
        self._drag_center_px = None
        self._dragging = False
        self.unsetCursor()
        if was_dragging:
            self._clusters_dirty = True
            self.view_changed.emit(self.center_lat, self.center_lon, self.zoom)
            return
        self._handle_click(event.position())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._skip_release = True
        pos = event.position()
        if self._spider is not None:
            # Stacked stations: the first click spread them out; only a station on a leg counts.
            leg = self._spider_member_at(pos)
            if leg is not None:
                self._open_station(leg)
            return
        hit = self._hit_marker(pos)
        if hit is not None:
            if hit[0] == "marker":
                self._open_station(hit[1])
            return
        if self.interactive:
            self.zoom_by(1, pos)

    def _open_station(self, marker: MapMarker) -> None:
        QToolTip.hideText()
        self._info_text = ""
        self.marker_double_clicked.emit(marker.payload if marker.payload is not None else marker)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().leaveEvent(event)
        if self._spider is not None:
            self._spider_collapse_timer.start(400)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if not self.interactive:
            return super().keyPressEvent(event)
        step = 80
        key = event.key()
        cx, cy = self._center_world()
        moves = {
            Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
            Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step),
        }
        if key in moves:
            dx, dy = moves[key]
            latitude, longitude = geo.unproject(cx + dx, cy + dy, self.zoom)
            self.set_view(latitude, longitude)
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_by(1)
            return
        if key == Qt.Key.Key_Minus:
            self.zoom_by(-1)
            return
        if key == Qt.Key.Key_Escape and self.layers.get("reception"):
            self.track_points_selected.emit([], QPoint())
            return
        super().keyPressEvent(event)

    def _handle_click(self, pos: QPointF) -> None:
        hit = self._hit_marker(pos)
        if hit is not None:
            kind, item = hit
            if kind == "cluster":
                cluster: _Cluster = item
                if self.zoom >= self.native_max_zoom:
                    self._open_spider(cluster.members, pos)
                    return
                self.fit_points([(member.latitude, member.longitude) for member in cluster.members], padding=40,
                                max_zoom=self.native_max_zoom)
                if self.zoom <= self.min_zoom:
                    self.zoom_by(1, pos)
                return
            marker: MapMarker = item
            if (
                self._spider is None
                and self.spiderfy_enabled
                and self.zoom >= self.spiderfy_activation_zoom()
                and len(self._nearby_markers(marker)) > 1
            ):
                self._open_spider(self._nearby_markers(marker), self._marker_screen(marker, self._origin()))
                return
            if marker.tooltip:
                self._info_text = marker.tooltip
                QToolTip.showText(self.mapToGlobal(pos.toPoint()), marker.tooltip, self, QRect(), INFO_WINDOW_MS)
            self.marker_clicked.emit(marker.payload if marker.payload is not None else marker)
            return
        if self._spider is not None:
            self._collapse_spider()
            return
        latitude, longitude = self.lat_lon_at(pos)
        self.map_clicked.emit(latitude, longitude)

    def _update_hover(self, pos: QPointF, global_pos: QPoint) -> None:
        if self._spider is not None:
            if self._spider["bounds"].contains(pos):
                self._spider_collapse_timer.stop()
            elif not self._spider_collapse_timer.isActive():
                self._spider_collapse_timer.start(400)
        if self._hit_ruler(pos) is not None:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        hit = self._hit_marker(pos)
        if hit is not None:
            kind, item = hit
            self.setCursor(Qt.CursorShape.PointingHandCursor)  # only the cursor: a station opens on a click
            if kind == "cluster":
                self._show_tooltip(global_pos, self._cluster_tooltip(item))
            else:
                marker: MapMarker = item
                self.marker_hovered.emit(marker.payload if marker.payload is not None else marker)
        else:
            dots = self._hit_points(pos)
            shape = None if dots else self._hit_shape(pos)
            if dots:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                tooltips = list(dict.fromkeys(dot.point_tooltips[index] for dot, index in dots))
                self._show_tooltip(global_pos, "<hr>".join(tooltips))
            elif shape is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor if isinstance(shape, MapPolyline) else Qt.CursorShape.ArrowCursor)
                self._show_tooltip(global_pos, shape.tooltip)
            else:
                self.unsetCursor()
                if not self._info_open():
                    QToolTip.hideText()

    def _info_open(self) -> bool:
        """Is the station info window of the last left click still up?"""
        if self._info_text and not (QToolTip.isVisible() and QToolTip.text() == self._info_text):
            self._info_text = ""  # gone: a click, the wheel, the mouse out of the map or the time
        return bool(self._info_text)

    def _show_tooltip(self, global_pos: QPoint, text: str) -> None:
        """Show it once: the mouse moves under a tooltip that is already up are left alone."""
        if self._info_open():
            return  # the station info a click opened stays while the mouse moves on
        if QToolTip.isVisible() and QToolTip.text() == text:
            return
        QToolTip.showText(global_pos, text, self)

    @staticmethod
    def _cluster_tooltip(cluster: _Cluster) -> str:
        callsigns = sorted({member.label or member.key for member in cluster.members}, key=str.casefold)
        visible = callsigns[:12]
        hidden = len(callsigns) - len(visible)
        items = "".join(f"<li>{_escape(name)}</li>" for name in visible)
        more = f"<li>+{hidden}</li>" if hidden > 0 else ""
        return f"<b>{len(callsigns)}</b><ul style='margin:0; padding-left:14px'>{items}{more}</ul>"


def _escape(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def escape_html(value: Any) -> str:
    return _escape(value)


def hsl_color(hue: int, saturation: int, lightness: int) -> QColor:
    return QColor.fromHslF(hue / 360.0, saturation / 100.0, lightness / 100.0)


def callsign_color(callsign: str) -> QColor:
    return hsl_color(*geo.callsign_color_hsl(callsign))


def overlay_contrast_color() -> QColor:
    colors = current_colors()
    if colors.is_dark:
        return QColor(255, 255, 255, round(0.92 * 255))
    return QColor(16, 24, 40, round(0.82 * 255))


def alert_area_color(name: str) -> QColor:
    """Leaflet named colors used for alert areas (yellow/orange/red/gray)."""
    return QColor({"yellow": "#ffff00", "orange": "#ffa500", "red": "#ff0000"}.get(str(name or "").lower(), "#808080"))


def geojson_polygons(feature_collection: dict[str, Any] | None) -> list[tuple[list[list[tuple[float, float]]], dict[str, Any]]]:
    """GeoJSON Polygon/MultiPolygon features -> [(rings as (lat, lon)), properties]."""
    result: list[tuple[list[list[tuple[float, float]]], dict[str, Any]]] = []
    for feature in (feature_collection or {}).get("features") or []:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        polygons: list[Any] = []
        if geometry_type == "Polygon":
            polygons = [coordinates]
        elif geometry_type == "MultiPolygon":
            polygons = list(coordinates)
        for polygon in polygons:
            rings: list[list[tuple[float, float]]] = []
            for ring in polygon or []:
                points = []
                for point in ring or []:
                    try:
                        points.append((float(point[1]), float(point[0])))
                    except (TypeError, ValueError, IndexError):
                        continue
                if len(points) >= 3:
                    rings.append(points)
            if rings:
                result.append((rings, properties))
    return result


def mask_color_for_theme() -> QColor:
    colors = current_colors()
    raw = colors.raw.get("map-mask-layer-color")
    if raw:
        return parse_css_color(raw, "#000000")
    return QColor(0, 0, 0)


# The mask list goes both ways (the user's choice: CARTO dark was too dark even at 0 %): a positive
# percent darkens the tiles with the theme's mask colour, a negative one lightens them with white.
MASK_LIGHTEN_PERCENTS = (50, 40, 30, 20, 10)


def map_mask(percent: int) -> tuple[QColor, float]:
    if percent < 0:
        return QColor(255, 255, 255), min(1.0, -percent / 100)
    return mask_color_for_theme(), min(1.0, percent / 100)


def mask_setting_key(source_id: int | None, *, dark: bool) -> str:
    """Window setting of the mask: one per map background (and theme), the theme's own as the fallback."""
    base = f"map/mask_opacity_{'dark' if dark else 'light'}"
    return f"{base}_{int(source_id)}" if source_id else base


def stored_mask_percent(context: Any, source_id: int | None, *, dark: bool) -> int:
    for key in (mask_setting_key(source_id, dark=dark), mask_setting_key(None, dark=dark)):
        try:
            value = int(context.ui_value(key))
        except (TypeError, ValueError):
            continue
        return int(geo.clamp(round(value / 10) * 10, -max(MASK_LIGHTEN_PERCENTS), 100))
    return default_mask_opacity_percent()


def default_mask_opacity_percent() -> int:
    colors = current_colors()
    try:
        value = float(colors.raw.get("map-mask-default-opacity") or (0.2 if colors.is_dark else 0.16))
    except ValueError:
        value = 0.2
    percent = max(0, min(100, round(value * 100)))
    return percent - percent % 10
