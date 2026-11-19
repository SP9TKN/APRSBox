"""Map page (web: templates/map.html + static/js/map*.js)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.desktop import geo, idle
from app.desktop.i18n import t
from app.desktop.icons import icon, icon_pixmap, symbol_icon_for, symbol_pixmap
from app.desktop.map_layers import (
    OWN_SETTINGS_ORIGIN,
    TrackReceptionLines,
    alert_area_shapes,
    coverage_shapes,
    own_station_marker,
    parse_float,
    path_kind_label,
    path_segment_color,
    path_shapes,
    station_markers,
    station_positions,
    track_shapes,
)
from app.desktop.rf_paths import (
    DEFAULT_RF_PATH_WINDOW_SECONDS,
    KIND_APRSIS,
    KIND_RF_DIGI,
    KIND_RF_DIRECT,
    RF_PATH_WINDOW_OPTIONS_SECONDS,
    base_callsign,
    build_segments,
    is_generic_alias,
    is_q_construct,
    load_recent_routes,
    normalize_callsign,
    own_station_identity,
    path_tokens,
    split_tnc2,
    used_path_tokens,
)
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors
from app.desktop.widgets.common import Page, clear_layout, icon_button, label
from app.desktop.tiles import shared_tile_manager
from app.desktop.widgets.map_view import (
    MASK_LIGHTEN_PERCENTS,
    MapView,
    escape_html,
    map_mask,
    mask_setting_key,
    stored_mask_percent,
)

STATION_REFRESH_MS = 10000
TRAFFIC_REFRESH_MS = 2000
# Nobody at the computer for 10 minutes (app.desktop.idle): slower refreshes, the user's choice.
# Receiving, the digipeater, the iGate and the alarm popups are not affected; the first mouse or
# key press brings the normal pace back with an immediate refresh.
IDLE_CHECK_MS = 5000
IDLE_STATION_REFRESH_MS = 30000
IDLE_TRAFFIC_REFRESH_MS = 10000
IDLE_ROUTES_REFRESH_MS = 5000
# "Reset view" on the own station: about 28 x 19 km in a normal window (a whole town and its
# surroundings).  The web's default zoom 10 showed half the region.
STATION_VIEW_ZOOM = 12
# Map toolbar sliding down from the top edge of the page (the user's choice): reveal strip at the
# top of the map, slide and hide timing.
TOP_BAR_REVEAL_PX = 6
TOP_BAR_SLIDE_MS = 160
TOP_BAR_HIDE_DELAY_MS = 900
SCROLLER_MAX_ENTRIES = 120
# Traffic history on the map (tracks with dots): as long as the history stored by default (6 h).
DEFAULT_TRACK_WINDOW_SECONDS = 21600
# Object history in the top bar: stations and objects heard within this window are on the map
# (the user's choice, 1 h by default). Separate from the tracks (Traffic history) and from the
# stored history (Settings), which only limits what there is to show.
OBJECT_WINDOW_OPTIONS_SECONDS = (300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 259200, 345600)
DEFAULT_OBJECT_WINDOW_SECONDS = 3600


def format_window(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def _heard_since(value: Any, cutoff: datetime) -> bool:
    """A track point heard at or after ``cutoff`` (points without a time are kept)."""
    try:
        heard = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    if heard.tzinfo is None:
        heard = heard.replace(tzinfo=timezone.utc)
    return heard >= cutoff


def _station_heard_since(station: dict[str, Any], cutoff: datetime) -> bool:
    """A station or object heard at or after ``cutoff``, over RF or APRS-IS (no time: kept)."""
    times = [value for value in (station.get("last_seen_any_at"), station.get("last_heard_at")) if value]
    return not times or any(_heard_since(value, cutoff) for value in times)


def stored_paths_window(context: Any) -> int:
    """Saved RF path window in seconds; older versions saved minutes under another key."""
    try:
        seconds = int(context.ui_value("map/rf_paths_window_seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0
    if not seconds:
        try:
            seconds = int(context.ui_value("map/rf_paths_window_minutes") or 0) * 60
        except (TypeError, ValueError):
            seconds = 0
    if not seconds:
        return DEFAULT_RF_PATH_WINDOW_SECONDS
    return min(RF_PATH_WINDOW_OPTIONS_SECONDS, key=lambda option: abs(option - seconds))


def _bool_setting(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


class OverlayPanel(QFrame):
    """Floating panel drawn over the map (latest packet / scroller / alarms)."""

    def __init__(self, parent: QWidget, object_name: str) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("card", "true")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class MapPage(Page):
    page_key = "map"
    scrollable = False

    def build(self) -> None:
        context = self.context
        self.body.setContentsMargins(12, 10, 12, 10)
        self.body.setSpacing(8)
        self._settings_prefix = "map/"

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.reset_button = icon_button("crosshairs-gps", t("Reset view"), self._reset_view)
        self.tracks_button = icon_button("track-light", t("Hide tracks"), self._toggle_tracks)
        self.coverage_button = icon_button("map-marker-radius", t("Hide coverage"), self._toggle_coverage)
        self.alarms_button = icon_button("alarm-light-outline", t("Show alarm list"), self._toggle_alarm_panel)
        self.latest_button = icon_button("message-alert", t("Hide latest packet widget"), self._toggle_latest)
        self.scroller_button = icon_button("format-list-bulleted", f"{t('Hide')} {t('Traffic Monitor')}", self._toggle_scroller)
        self.ruler_button = icon_button("ruler", t("Hide ruler"), self._toggle_ruler)
        self.grid_button = icon_button("grid", t("Hide Maidenhead grid"), self._toggle_grid)
        self.paths_button = icon_button("transit-connection-variant", t("Show RF paths"), self._toggle_paths)
        for widget in (
            self.reset_button, self.tracks_button, self.coverage_button, self.alarms_button, self.latest_button,
            self.scroller_button, self.ruler_button, self.grid_button, self.paths_button,
        ):
            toolbar.addWidget(widget)
        self.mask_combo = QComboBox()
        # Shown as the user reads it: + lighter, - darker (the stored value is the darkening percent,
        # negative = lighter, so the settings saved before keep their meaning).
        for value in [-percent for percent in MASK_LIGHTEN_PERCENTS] + list(range(0, 101, 10)):
            self.mask_combo.addItem(f"{-value:+d}%" if value else "0%", value)
        self.mask_combo.setToolTip(t("Map background: + lighter, - darker (remembered for each map)"))
        self.mask_combo.currentIndexChanged.connect(self._mask_changed)
        toolbar.addWidget(self.mask_combo)
        # Map background picked on the bar (the user's choice): the enabled map sources of Settings.
        # Only this window remembers it; the default source in Settings stays as it is.
        self.tiles_combo = QComboBox()
        # The names come in later (activate): the width follows them, or the list shows "Wysokc".
        self.tiles_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.tiles_combo.setToolTip(t("Map background"))
        self.tiles_combo.currentIndexChanged.connect(self._tiles_source_changed)
        toolbar.addWidget(self.tiles_combo)
        # Quick traffic history on the map (the tracks with dots), same times as the RF lines. Only what
        # the map shows: the stored history stays as set in Settings (a short value there deletes frames).
        history_tip = t("Traffic history on the map: tracks heard within")
        self.history_icon = QLabel()
        self.history_icon.setPixmap(icon_pixmap("history", None, 18))
        self.history_icon.setToolTip(history_tip)
        toolbar.addWidget(self.history_icon)
        self.history_window_combo = QComboBox()
        for seconds in RF_PATH_WINDOW_OPTIONS_SECONDS:
            self.history_window_combo.addItem(format_window(seconds), seconds)
        self.history_window_combo.setToolTip(history_tip)
        self.history_window_combo.currentIndexChanged.connect(self._history_window_changed)
        toolbar.addWidget(self.history_window_combo)
        self.objects_window_combo = QComboBox()
        for seconds in OBJECT_WINDOW_OPTIONS_SECONDS:
            self.objects_window_combo.addItem(format_window(seconds), seconds)
        self.objects_window_combo.setToolTip(t("Object history on the map: stations and objects heard within"))
        self.objects_window_combo.currentIndexChanged.connect(self._objects_window_changed)
        toolbar.addWidget(self.objects_window_combo)
        self.paths_window_combo = QComboBox()
        for seconds in RF_PATH_WINDOW_OPTIONS_SECONDS:
            self.paths_window_combo.addItem(format_window(seconds), seconds)
        self.paths_window_combo.setToolTip(t("RF paths: frames heard within"))
        self.paths_window_combo.currentIndexChanged.connect(self._paths_window_changed)
        toolbar.addWidget(self.paths_window_combo)
        self.paths_rf_check = QCheckBox(t("RF"))
        self.paths_rf_check.setToolTip(t("Paths of frames heard by my station over RF"))
        self.paths_is_check = QCheckBox(t("APRS-IS"))
        self.paths_is_check.setToolTip(t("Paths of APRS-IS frames to the IGate that heard them (qAR/qAO)"))
        self.paths_rf_check.toggled.connect(self._paths_filter_changed)
        self.paths_is_check.toggled.connect(self._paths_filter_changed)
        toolbar.addWidget(self.paths_rf_check)
        toolbar.addWidget(self.paths_is_check)
        # One row, never wrapped (the user's choice): the bar keeps one height; when the row is too
        # narrow the interface chips show only their band (fit_toolbar_row).
        self.filters_host = QWidget()
        self.filters_layout = QHBoxLayout(self.filters_host)
        self.filters_layout.setContentsMargins(0, 0, 0, 0)
        self.filters_layout.setSpacing(6)
        toolbar.addWidget(self.filters_host)
        toolbar.addStretch(1)
        self.toolbar_row = toolbar
        self._toolbar_fit_key: tuple[Any, ...] | None = None

        info = QHBoxLayout()
        info.setSpacing(18)
        self.tile_label = label("", "small")
        self.center_label = label("", "small")
        self.zoom_label = label("", "small")
        self.paths_legend = label("", "small")
        self.paths_legend.setTextFormat(Qt.TextFormat.RichText)
        for widget in (self.tile_label, self.center_label, self.zoom_label):
            info.addWidget(widget)
        info.addStretch(1)
        info.addWidget(self.paths_legend)

        try:
            stored_source = int(context.ui_value("map/tile_source_id") or 0)
        except (TypeError, ValueError):
            stored_source = 0
        shared_tile_manager().set_preferred_source(stored_source or None)  # before the map loads tiles
        self.map = MapView(self)
        self.map.view_changed.connect(self._on_view_changed)
        self.map.marker_double_clicked.connect(self._open_station)  # one click: the info window only
        self.reception_lines = TrackReceptionLines(
            self.map, positions=self._known_positions, own_key=lambda: own_station_identity(self.station_settings))
        self.map.tile_status_changed.connect(self._on_tile_status)
        self.body.addWidget(self.map, 1)

        # The toolbar and the info line slide down from the top edge of the page, full width and in
        # the window's colour (the user's choice), only while the mouse is at that edge: the map
        # gets the whole height.  Same margins as the page, so the bar looks as before when out.
        self.top_bar = QFrame(self.body_widget)
        self.top_bar.setObjectName("MapTopBar")
        self.top_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        margins = self.body.contentsMargins()
        top_bar_layout = QVBoxLayout(self.top_bar)
        # The icons stay where they were; the info line sits right under them, so the bar is low.
        top_bar_layout.setContentsMargins(margins.left(), margins.top(), margins.right(), 3)
        top_bar_layout.setSpacing(0)
        top_bar_layout.addLayout(toolbar)
        top_bar_layout.addLayout(info)
        self.top_bar.show()  # PageBody may already be on screen: a child added later stays hidden otherwise
        # Captions under the time lists (the user's choice), in the free part of the info line so
        # the bar keeps its height; placed by hand under each list (_place_toolbar_captions).
        self.toolbar_captions = [
            (self.tiles_combo, label(t("Map"), "small")),
            (self.history_window_combo, label(t("Traffic history"), "small")),
            (self.objects_window_combo, label(t("Object history"), "small")),
            (self.paths_window_combo, label(t("RF paths"), "small")),
        ]
        self._placing_captions = False
        self._captions_dirty = False
        for combo, caption in self.toolbar_captions:
            caption.setParent(self.top_bar)
            caption.setToolTip(combo.toolTip())
            caption.show()
        self._caption_watch = (self.tiles_combo, self.history_window_combo, self.objects_window_combo,
                               self.paths_window_combo, self.zoom_label, self.paths_legend)
        for widget in self._caption_watch:
            widget.installEventFilter(self)
        self.top_bar_shown = False
        self._top_bar_animation = QPropertyAnimation(self.top_bar, b"pos", self)
        self._top_bar_animation.setDuration(TOP_BAR_SLIDE_MS)
        self._top_bar_hide_timer = QTimer(self)
        self._top_bar_hide_timer.setSingleShot(True)
        self._top_bar_hide_timer.setInterval(TOP_BAR_HIDE_DELAY_MS)
        self._top_bar_hide_timer.timeout.connect(self._hide_top_bar_when_idle)
        self.body_widget.setMouseTracking(True)
        self.body_widget.installEventFilter(self)

        # Overlays (children of the map so they float above it).
        self.alarm_panel = OverlayPanel(self.map, "MapAlarmPanel")
        alarm_layout = QVBoxLayout(self.alarm_panel)
        alarm_layout.setContentsMargins(10, 8, 10, 10)
        alarm_header = QHBoxLayout()
        alarm_header.addWidget(label(t("Active alarms"), "h3"), 1)
        alarm_header.addWidget(icon_button("close", t("Close"), lambda: self._set_alarm_panel_open(False), size=14))
        alarm_layout.addLayout(alarm_header)
        self.alarm_list_host = QWidget()
        self.alarm_list = QVBoxLayout(self.alarm_list_host)
        self.alarm_list.setContentsMargins(0, 0, 0, 0)
        self.alarm_list.setSpacing(6)
        alarm_scroll = QScrollArea()
        alarm_scroll.setWidgetResizable(True)
        alarm_scroll.setFrameShape(QFrame.Shape.NoFrame)
        alarm_scroll.setWidget(self.alarm_list_host)
        alarm_layout.addWidget(alarm_scroll, 1)

        self.latest_panel = OverlayPanel(self.map, "MapLatestPanel")
        latest_layout = QVBoxLayout(self.latest_panel)
        latest_layout.setContentsMargins(10, 8, 10, 8)
        latest_layout.setSpacing(3)
        self.latest_title = label(t("Latest packet"), "h3")
        latest_layout.addWidget(self.latest_title)
        self.latest_body = label("", None, wrap=True, selectable=True)
        self.latest_body.setTextFormat(Qt.TextFormat.RichText)
        latest_layout.addWidget(self.latest_body)

        self.scroller_panel = OverlayPanel(self.map, "MapScrollerPanel")
        scroller_layout = QVBoxLayout(self.scroller_panel)
        scroller_layout.setContentsMargins(4, 4, 4, 4)
        self.scroller_list = QListWidget()
        self.scroller_list.setFrameShape(QFrame.Shape.NoFrame)
        self.scroller_list.setIconSize(QSize(18, 18))
        self.scroller_list.setStyleSheet("QListWidget { background: transparent; border: none; } QListWidget::item { padding: 1px 2px; }")
        self.scroller_list.itemClicked.connect(self._on_scroller_clicked)
        scroller_layout.addWidget(self.scroller_list)

        self.map.installEventFilter(self)

        # State ---------------------------------------------------------------
        self.tracks_visible = _bool_setting(context.ui_value("map/tracks_visible"), True)
        self.coverage_visible = _bool_setting(context.ui_value("map/coverage_visible"), True)
        self.alarm_panel_open = _bool_setting(context.ui_value("map/alerts_overlay_open"), False)
        self.latest_visible = _bool_setting(context.ui_value("map/latest_overlay_visible"), True)
        self.scroller_visible = _bool_setting(context.ui_value("map/scroller_visible"), True)
        self.ruler_visible = _bool_setting(context.ui_value("map/ruler_visible"), True)
        self.grid_visible = _bool_setting(context.ui_value("map/maidenhead_grid_visible"), True)
        self.paths_visible = _bool_setting(context.ui_value("map/rf_paths_visible"), False)
        self.paths_window = stored_paths_window(context)  # seconds
        try:
            self.history_window = int(context.ui_value("map/track_window_seconds") or DEFAULT_TRACK_WINDOW_SECONDS)
        except (TypeError, ValueError):
            self.history_window = DEFAULT_TRACK_WINDOW_SECONDS
        if self.history_window not in RF_PATH_WINDOW_OPTIONS_SECONDS:
            self.history_window = DEFAULT_TRACK_WINDOW_SECONDS
        try:
            self.objects_window = int(context.ui_value("map/object_window_seconds") or DEFAULT_OBJECT_WINDOW_SECONDS)
        except (TypeError, ValueError):
            self.objects_window = DEFAULT_OBJECT_WINDOW_SECONDS
        if self.objects_window not in OBJECT_WINDOW_OPTIONS_SECONDS:
            self.objects_window = DEFAULT_OBJECT_WINDOW_SECONDS
        for widget, key in ((self.paths_rf_check, "map/rf_paths_rf"), (self.paths_is_check, "map/rf_paths_aprsis")):
            widget.blockSignals(True)
            widget.setChecked(_bool_setting(context.ui_value(key), True))
            widget.blockSignals(False)
        try:
            hidden = json.loads(str(context.ui_value("map/hidden_alert_ids", "[]") or "[]"))
            self.hidden_alert_ids = {int(value) for value in hidden}
        except (ValueError, TypeError):
            self.hidden_alert_ids = set()
        self.hidden_interface_keys: set[str] = set()
        self.payload: dict[str, Any] = {}
        self.station_settings: dict[str, Any] = {}
        self.own_symbol_icon = ""
        self.map_config: dict[str, Any] = {}
        self.routes: list[Any] = []
        self._routes_signature: list[Any] | None = None
        self.traffic: dict[str, Any] = {}
        self._view_initialized = False
        self._last_station_signature = ""
        self._interfaces: list[dict[str, Any]] = []

        self.history_window_combo.blockSignals(True)
        self.history_window_combo.setCurrentIndex(self.history_window_combo.findData(self.history_window))
        self.history_window_combo.blockSignals(False)
        self.objects_window_combo.blockSignals(True)
        self.objects_window_combo.setCurrentIndex(self.objects_window_combo.findData(self.objects_window))
        self.objects_window_combo.blockSignals(False)
        index = self.paths_window_combo.findData(self.paths_window)
        self.paths_window_combo.blockSignals(True)
        self.paths_window_combo.setCurrentIndex(index if index >= 0 else self.paths_window_combo.findData(DEFAULT_RF_PATH_WINDOW_SECONDS))
        self.paths_window_combo.blockSignals(False)

        # RF paths reload on their own clock: a 1-30 s window needs seconds, not the 10 s station refresh.
        self._routes_loader = SerialLoader(self._load_routes, self._apply_routes, self)
        self._routes_timer = QTimer(self)
        self._routes_timer.timeout.connect(self._routes_loader.request)
        self._station_loader = SerialLoader(self._load_stations, self._apply_stations, self)
        self._traffic_loader = SerialLoader(self._load_traffic, self._apply_traffic, self)
        self._station_timer = QTimer(self)
        self._station_timer.timeout.connect(self._station_loader.request)
        self._traffic_timer = QTimer(self)
        self._traffic_timer.timeout.connect(self._traffic_loader.request)
        self._idle = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self._check_idle)
        self._apply_toggle_states()
        self._apply_mask_from_settings()

    # ------------------------------------------------------------ lifecycle
    def activate(self, argument: Any = None) -> None:
        self.ensure_built()
        self._visible_on_screen = True
        self._fill_tiles_combo()  # map sources may have changed in Settings
        self.map.reload_tile_source()
        self._update_tile_label("")
        self._station_loader.request()
        self._traffic_loader.request()
        self._idle = idle.nobody_at_computer()
        self._station_timer.start(IDLE_STATION_REFRESH_MS if self._idle else STATION_REFRESH_MS)
        self._traffic_timer.start(IDLE_TRAFFIC_REFRESH_MS if self._idle else TRAFFIC_REFRESH_MS)
        self._idle_timer.start()
        self._sync_routes_timer()
        self.open_argument(argument)

    def deactivate(self) -> None:
        super().deactivate()
        if self._built:
            self._station_timer.stop()
            self._traffic_timer.stop()
            self._routes_timer.stop()
            self._idle_timer.stop()

    def _check_idle(self) -> None:
        nobody = idle.nobody_at_computer()
        if nobody == self._idle:
            return
        self._idle = nobody
        # setInterval restarts a running timer with the new pace.
        self._station_timer.setInterval(IDLE_STATION_REFRESH_MS if nobody else STATION_REFRESH_MS)
        self._traffic_timer.setInterval(IDLE_TRAFFIC_REFRESH_MS if nobody else TRAFFIC_REFRESH_MS)
        self._sync_routes_timer()
        if not nobody:  # somebody is back: the current state at once, not at the next slow tick
            self._station_loader.request()
            self._traffic_loader.request()

    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict):
            latitude = parse_float(argument.get("latitude"))
            longitude = parse_float(argument.get("longitude"))
            if latitude is not None and longitude is not None:
                self._view_initialized = True
                self.map.center_on(latitude, longitude, int(argument.get("zoom") or 15), anchor=self._visible_map_center())

    def refresh(self) -> None:
        if self._built:
            self._station_loader.request()

    # --------------------------------------------------------------- loading
    def _load_stations(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.content import get_station_settings
        from app.services.map_service import get_map_page_config, get_map_station_payload

        with connection_scope():
            station_settings = get_station_settings(include_tx_runtime=False)
            config = get_map_page_config(station_settings=station_settings)
            payload = get_map_station_payload()
            own_symbol_icon = symbol_icon_for(station_settings.get("symbol_table"), station_settings.get("symbol_code"))
        return {"settings": station_settings, "config": config, "payload": payload, "own_symbol_icon": own_symbol_icon}

    def _apply_stations(self, result: dict[str, Any]) -> None:
        self.station_settings = result.get("settings") or {}
        self.own_symbol_icon = str(result.get("own_symbol_icon") or "")
        self.map_config = result.get("config") or {}
        self.payload = result.get("payload") or {}
        config = self.map_config
        from app.services.content import get_aprs_symbol_set

        try:
            modern = get_aprs_symbol_set() == "modern"
        except Exception:
            modern = False
        self.map.set_modern_symbols(modern)
        self.map.set_clustering(
            bool(config.get("marker_clustering_enabled")),
            spiderfy=bool(config.get("marker_spiderfy_enabled")),
            levels_before_max=int(config.get("marker_spiderfy_zoom_levels_before_max") or 2),
            nearby_px=int(config.get("marker_spiderfy_nearby_distance_px") or 20),
        )
        if not self._view_initialized:
            self._view_initialized = True
            stored = self.context.ui_value("map/view")
            view = None
            if stored:
                try:
                    view = json.loads(str(stored))
                except ValueError:
                    view = None
            if isinstance(view, dict) and all(key in view for key in ("latitude", "longitude", "zoom")):
                self.map.set_view(float(view["latitude"]), float(view["longitude"]), int(view["zoom"]))
            else:
                self._reset_view(persist=False)
        self._render_all()

    def _routes_interval_ms(self) -> int:
        if self.paths_window <= 60:
            interval = 1000
        else:
            interval = 3000 if self.paths_window <= 1800 else STATION_REFRESH_MS
        return max(interval, IDLE_ROUTES_REFRESH_MS) if self._idle else interval

    def _sync_routes_timer(self) -> None:
        if self.paths_visible and getattr(self, "_visible_on_screen", False):
            self._routes_timer.start(self._routes_interval_ms())
            self._routes_loader.request()
        else:
            self._routes_timer.stop()

    def _load_routes(self) -> list[Any]:
        from app.db import connection_scope
        from app.services.content import get_station_settings

        if not self.paths_visible:
            return []
        with connection_scope():
            station_settings = self.station_settings or get_station_settings(include_tx_runtime=False)
            return load_recent_routes(self.paths_window, own_station_identity(station_settings))

    def _apply_routes(self, routes: list[Any]) -> None:
        routes = list(routes or [])
        # Short windows reload every second; the map is repainted only when a path came or went.
        signature = [(route.station, tuple(route.hops), route.receiver, route.kind, route.heard_at, route.interface_id)
                     for route in routes]
        if signature == self._routes_signature:
            return
        self._routes_signature = signature
        self.routes = routes
        self._render_paths()

    def _load_traffic(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.content import traffic_snapshot

        # One connection for the whole snapshot (every 2 s): it reads many settings, some per frame,
        # and a new connection for each was most of the program's CPU time (py-spy, 2026-10-09).
        with connection_scope():
            return traffic_snapshot(limit=SCROLLER_MAX_ENTRIES * 2)

    def _apply_traffic(self, snapshot: dict[str, Any]) -> None:
        self.traffic = snapshot or {}
        self._render_scroller_and_latest()

    # -------------------------------------------------------------- rendering
    def _interfaces_from_payload(self) -> list[dict[str, Any]]:
        return list(self.payload.get("interfaces") or [])

    @staticmethod
    def _interface_key(item: dict[str, Any]) -> str:
        interface_id = item.get("modem_id")
        if isinstance(interface_id, int):
            return f"modem:{interface_id}"
        return f"fallback:{item.get('name') or ''}:{item.get('band') or ''}"

    def _visibility_context(self) -> tuple[set[int], set[int]]:
        known: set[int] = set()
        visible: set[int] = set()
        for item in self._interfaces:
            interface_id = item.get("modem_id")
            if not isinstance(interface_id, int):
                continue
            known.add(interface_id)
            if self._interface_key(item) not in self.hidden_interface_keys:
                visible.add(interface_id)
        return known, visible

    def _filtered_data(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """map.js ``filteredMapData``: honor the interface filter chips."""
        known, visible = self._visibility_context()

        def interface_visible(value: Any) -> bool:
            try:
                interface_id = int(value)
            except (TypeError, ValueError):
                return True
            if interface_id not in known:
                return True
            return interface_id in visible

        tracks_out: list[dict[str, Any]] = []
        visible_points: dict[str, list[dict[str, Any]]] = {}
        # Traffic history window of the top bar: only the drawn tracks; the markers have their own hour.
        now = datetime.now(timezone.utc)
        history_cutoff = now - timedelta(seconds=self.history_window)
        station_cutoff = now - timedelta(seconds=self.objects_window)
        own_key = own_station_identity(self.station_settings).upper()
        for track in self.payload.get("mobile_tracks") or []:
            rebuilt: list[dict[str, Any]] = []
            for point in track.get("points") or []:
                if not interface_visible(point.get("interface_id")):
                    continue
                if rebuilt and abs(float(rebuilt[-1]["latitude"]) - float(point["latitude"])) < 1e-6 and abs(float(rebuilt[-1]["longitude"]) - float(point["longitude"])) < 1e-6:
                    rebuilt[-1] = point
                    continue
                rebuilt.append(point)
            rebuilt = rebuilt[-60:]
            key = str(track.get("display_callsign") or "").strip()
            if key and rebuilt:
                visible_points[key] = rebuilt
            recent = [point for point in rebuilt if _heard_since(point.get("heard_at"), history_cutoff)]
            if len(recent) >= 2:
                tracks_out.append({**track, "points": recent})
        stations_out: list[dict[str, Any]] = []
        interfaces_by_id = {item.get("modem_id"): item for item in self._interfaces}
        for station in self.payload.get("stations") or []:
            key = str(station.get("display_callsign") or station.get("callsign") or "").strip()
            if key.upper() != own_key and not _station_heard_since(station, station_cutoff):
                continue
            points = visible_points.get(key) or []
            if points:
                latest = points[-1]
                interface_item = interfaces_by_id.get(latest.get("interface_id"))
                stations_out.append(
                    {
                        **station,
                        "latitude": float(latest["latitude"]),
                        "longitude": float(latest["longitude"]),
                        "interface_id": latest.get("interface_id"),
                        "source": str((interface_item or {}).get("name") or station.get("source") or ""),
                        "last_heard_at": latest.get("heard_at") or station.get("last_heard_at"),
                    }
                )
                continue
            if interface_visible(station.get("interface_id")):
                stations_out.append(station)
        return stations_out, tracks_out

    def _render_all(self) -> None:
        self._interfaces = self._interfaces_from_payload()
        self._render_interface_filters()
        stations, tracks = self._filtered_data()
        self._visible_stations = stations
        own_key = own_station_identity(self.station_settings)
        markers = station_markers(stations, own_key=own_key, pick_hint=True)
        present = {
            str(station.get("display_callsign") or station.get("callsign") or "").strip().upper()
            for station in stations
            if parse_float(station.get("latitude")) is not None and parse_float(station.get("longitude")) is not None
        }
        own_marker = own_station_marker(self.station_settings, symbol_icon=self.own_symbol_icon, present_keys=present)
        if own_marker is not None:
            markers.append(own_marker)
        self.map.set_markers(markers)
        raw_fill = self.map_config.get("coverage_fill_opacity")
        try:
            fill_percent = int(raw_fill) if raw_fill is not None and str(raw_fill).strip() != "" else 5
        except (TypeError, ValueError):
            fill_percent = 5
        fill_opacity = max(0, min(20, fill_percent)) / 100  # 0 % is a valid choice (outline only)
        try:
            outline_percent = int(self.context.ui_value("map/coverage_outline_opacity", 100))
        except (TypeError, ValueError):
            outline_percent = 100
        outline_opacity = max(0, min(100, outline_percent)) / 100
        self.map.set_layer("coverage", coverage_shapes(stations, fill_opacity=fill_opacity, outline_opacity=outline_opacity)
                           if self.coverage_visible else [])
        interface_names = {item.get("modem_id"): str(item.get("name") or "") for item in self._interfaces}
        self.map.set_layer("tracks", track_shapes(tracks, own_key=own_key, interface_names=interface_names, pick_hint=True)
                           if self.tracks_visible else [])
        alert_areas = self.payload.get("alert_areas") or {}
        self.map.set_layer("areas", alert_area_shapes(alert_areas, self.hidden_alert_ids) if self.alarm_panel_open else [])
        self._render_alarm_panel(alert_areas)
        self._render_paths(stations)
        if self.tracks_visible:
            self.reception_lines.refresh()
        else:
            self.reception_lines.clear()
        self._render_scroller_and_latest()

    def _known_positions(self) -> dict[str, tuple[float, float]]:
        """Station positions for RF paths and reception lines, the own one from My Station."""
        positions = station_positions(self.payload.get("stations") or [])
        own_key = own_station_identity(self.station_settings)
        own_lat = parse_float(self.station_settings.get("latitude"))
        own_lon = parse_float(self.station_settings.get("longitude"))
        if own_key and own_lat is not None and own_lon is not None:
            positions[own_key] = (own_lat, own_lon)
        return positions

    def _render_paths(self, stations: list[dict[str, Any]] | None = None) -> None:
        if not self.paths_visible:
            self.map.set_layer("paths", [])
            self.paths_legend.setText("")
            return
        stations = stations if stations is not None else getattr(self, "_visible_stations", [])
        positions = self._known_positions()
        own_lat = parse_float(self.station_settings.get("latitude"))
        own_lon = parse_float(self.station_settings.get("longitude"))
        known, visible = self._visibility_context()
        segments = build_segments(
            self.routes,
            positions,
            include_rf=self.paths_rf_check.isChecked(),
            include_aprsis=self.paths_is_check.isChecked(),
            visible_interface_ids=visible,
            known_interface_ids=known,
        )
        self.map.set_layer("paths", path_shapes(segments))
        counts = {KIND_RF_DIRECT: 0, KIND_RF_DIGI: 0, KIND_APRSIS: 0}
        for segment in segments:
            counts[segment.kind] = counts.get(segment.kind, 0) + 1
        parts = []
        for kind in (KIND_RF_DIRECT, KIND_RF_DIGI, KIND_APRSIS):
            color = path_segment_color(kind).name()
            parts.append(f"<span style='color:{color}'>━━</span> {escape_html(path_kind_label(kind))} ({counts.get(kind, 0)})")
        if own_lat is None or own_lon is None:
            parts.append(f"<i>{escape_html(t('Set your station position in My Station to draw RF paths to it.'))}</i>")
        self.paths_legend.setText(" &nbsp; ".join(parts))

    def _render_interface_filters(self) -> None:
        clear_layout(self.filters_layout)
        self._toolbar_fit_key = None
        for item in self._interfaces:
            key = self._interface_key(item)
            visible = key not in self.hidden_interface_keys
            chip = QToolButton()
            chip.setProperty("variant", "secondary")
            chip.setCheckable(True)
            chip.setChecked(visible)
            chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            chip.setIcon(icon("eye" if visible else "eye-off", None, 14))
            full_text = f"{item.get('band') or '-'}  {item.get('name') or '-'}"
            chip.setText(full_text)
            chip.setProperty("full_text", full_text)
            chip.setProperty("short_text", str(item.get("band") or item.get("name") or "-"))
            chip.setToolTip(f"{full_text} · {t('Hide') if visible else t('Show')}")
            chip.clicked.connect(lambda _checked=False, interface_key=key: self._toggle_interface(interface_key))
            self.filters_layout.addWidget(chip)
            chip.show()
        self.filters_layout.invalidate()

    def _fit_toolbar_row(self, width: int) -> None:
        """Keep the toolbar in one row: full chip names when they fit, else only the band, else only
        the eye icon (the name stays in the tooltip)."""
        chips = [self.filters_layout.itemAt(index).widget() for index in range(self.filters_layout.count())]
        chips = [chip for chip in chips if chip is not None]
        key = (width, tuple(str(chip.property("full_text")) for chip in chips))
        if key == self._toolbar_fit_key:
            return
        self._toolbar_fit_key = key
        margins = self.top_bar.layout().contentsMargins()
        available = width - margins.left() - margins.right()
        # Summed from the widgets themselves: the layouts cache their sizes until the next event loop pass.
        spacing = self.toolbar_row.spacing()
        others = [
            self.toolbar_row.itemAt(index).widget() for index in range(self.toolbar_row.count())
        ]
        fixed = sum(widget.sizeHint().width() + spacing for widget in others
                    if widget is not None and widget is not self.filters_host and widget.isVisibleTo(self.top_bar))
        for level in ("full_text", "short_text", None):
            for chip in chips:
                chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if level
                                        else Qt.ToolButtonStyle.ToolButtonIconOnly)
                chip.setText(str(chip.property(level)) if level else "")
            needed = fixed + sum(chip.sizeHint().width() for chip in chips) + self.filters_layout.spacing() * max(0, len(chips) - 1)
            if needed <= available:
                break
        self.filters_layout.invalidate()
        self.filters_host.updateGeometry()
        self.toolbar_row.invalidate()

    def _toggle_interface(self, key: str) -> None:
        if key in self.hidden_interface_keys:
            self.hidden_interface_keys.discard(key)
        else:
            self.hidden_interface_keys.add(key)
        self._render_all()

    # ---------------------------------------------------------- alarm panel
    def _render_alarm_panel(self, alert_areas: dict[str, Any]) -> None:
        from app.desktop.widgets.common import clear_layout

        alerts = list((alert_areas or {}).get("alerts") or [])
        active_ids = {int(alert["id"]) for alert in alerts if str(alert.get("id", "")).isdigit()}
        retained = {alert_id for alert_id in self.hidden_alert_ids if alert_id in active_ids}
        if retained != self.hidden_alert_ids:
            self.hidden_alert_ids = retained
            self._persist_hidden_alerts()
        clear_layout(self.alarm_list)
        if not alerts:
            self.alarm_list.addWidget(label(t("No active alarms with map areas."), "muted", wrap=True))
        for alert in alerts:
            alert_id = int(alert.get("id") or 0)
            has_geometry = bool(alert.get("has_geometry"))
            card = QFrame()
            card.setProperty("card", "soft")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(2)
            heading = QHBoxLayout()
            badge = QLabel()
            event_icon = str(alert.get("event_icon") or "alert-outline.svg")
            badge.setPixmap(icon_pixmap(event_icon.removesuffix(".svg"), current_colors().color("warning"), 16))
            heading.addWidget(badge)
            heading.addWidget(label(str(alert.get("event_code") or t("Active alarms")), "h3"), 1)
            toggle = QCheckBox()
            toggle.setChecked(has_geometry and alert_id not in self.hidden_alert_ids)
            toggle.setEnabled(has_geometry)
            toggle.setToolTip(t("Visible on map") if has_geometry else t("No map area available"))
            toggle.toggled.connect(lambda checked, value=alert_id: self._set_alert_visible(value, checked))
            heading.addWidget(toggle)
            layout.addLayout(heading)
            source = " · ".join(part for part in (str(alert.get("source_callsign") or ""), str(alert.get("alarm_group") or "")) if part)
            layout.addWidget(label(source, "small"))
            meta: list[str] = []
            severity = alert.get("severity_level")
            if severity not in (None, ""):
                meta.append(f"{t('Severity level')}: {severity}")
            meta.append(f"{t('Area count')}: {int(alert.get('area_count') or 0)}")
            if alert.get("expires_at"):
                from app.datetime_utils import format_display_datetime

                meta.append(f"{t('Expires at')}: {format_display_datetime(alert.get('expires_at'))}")
            if not has_geometry:
                meta.append(t("No map area available"))
            layout.addWidget(label("  ·  ".join(meta), "small", wrap=True))
            details = QPushButton(t("Details"))
            details.setProperty("variant", "link")
            details.clicked.connect(lambda _checked=False, value=alert_id: self.navigate("alert-detail", value))
            layout.addWidget(details, 0, Qt.AlignmentFlag.AlignLeft)
            self.alarm_list.addWidget(card)
        self.alarm_list.addStretch(1)
        self._sync_alarm_button(alerts)

    def _sync_alarm_button(self, alerts: list[dict[str, Any]]) -> None:
        with_geometry = [alert for alert in alerts if alert.get("has_geometry")]
        visible = [alert for alert in with_geometry if int(alert.get("id") or 0) not in self.hidden_alert_ids]
        off = not self.alarm_panel_open or (with_geometry and not visible)
        self.alarms_button.setIcon(icon("alarm-light-off-outline" if off else "alarm-light-outline", None, 18))
        self.alarms_button.setToolTip(t("Hide alarm list") if self.alarm_panel_open else t("Show alarm list"))

    def _set_alert_visible(self, alert_id: int, visible: bool) -> None:
        if visible:
            self.hidden_alert_ids.discard(alert_id)
        else:
            self.hidden_alert_ids.add(alert_id)
        self._persist_hidden_alerts()
        self.map.set_layer("areas", alert_area_shapes(self.payload.get("alert_areas") or {}, self.hidden_alert_ids) if self.alarm_panel_open else [])
        self._sync_alarm_button(list((self.payload.get("alert_areas") or {}).get("alerts") or []))

    def _persist_hidden_alerts(self) -> None:
        self.context.set_ui_value("map/hidden_alert_ids", json.dumps(sorted(self.hidden_alert_ids)))

    # ------------------------------------------------- scroller / latest packet
    def _render_scroller_and_latest(self) -> None:
        stations = getattr(self, "_visible_stations", None) or list(self.payload.get("stations") or [])
        lookup: dict[str, dict[str, Any]] = {}
        for station in stations:
            for key in (station.get("display_callsign"), station.get("callsign")):
                text = normalize_callsign(key)
                if text:
                    lookup[text] = station
        own_key = own_station_identity(self.station_settings)
        own_base = base_callsign(own_key)
        entries = self._scroller_entries(lookup, own_key, own_base)
        self._render_scroller(entries)
        latest_station = None
        if entries:
            latest_station = lookup.get(normalize_callsign(entries[0]["station"])) or {"display_callsign": entries[0]["station"]}
        elif stations:
            latest_station = next((station for station in stations if parse_float(station.get("latitude")) is not None), None)
        self._render_latest(latest_station)
        self._position_overlays()

    def _scroller_entries(self, lookup: dict[str, dict[str, Any]], own_key: str, own_base: str) -> list[dict[str, Any]]:
        def is_own(callsign: str) -> bool:
            full = normalize_callsign(callsign)
            base = base_callsign(full)
            return bool(own_key) and (full in {own_key, own_base} or base in {own_key, own_base})

        entries: list[dict[str, Any]] = []
        for frame in self.traffic.get("frames") or []:
            direction = str(frame.get("direction") or "").strip().upper()
            parsed = split_tnc2(str(frame.get("line") or ""))
            if parsed is None:
                continue
            info = parsed["info"]
            third_party = info.startswith("}")
            inner = split_tnc2(info[1:].lstrip()) if third_party else None
            station = str((inner or parsed)["source"]).strip()
            if not station:
                continue
            own = is_own(station)
            if direction != "RX" and not (direction == "TX" and own):
                continue
            used = used_path_tokens(path_tokens(parsed["path"]))
            digi_label = "-"
            if third_party:
                digi_label = self._digi_label(parsed["source"])
            else:
                for token in reversed(used):
                    if is_q_construct(token) or is_generic_alias(token):
                        continue
                    digi_label = self._digi_label(token)
                    break
                else:
                    if used:
                        digi_label = self._digi_label(used[-1])
            digipeated_by_local = any(is_own(token) for token in used if not is_q_construct(token))
            if direction == "TX" and own:
                marker = "@"
            elif digipeated_by_local:
                marker = "@"
            elif third_party:
                marker = "#"
            elif not used:
                marker = "*"
            else:
                marker = ""
            display = str(frame.get("display_callsign") or station).strip() or station
            entries.append(
                {
                    "timestamp": str(frame.get("timestamp") or ""),
                    "station": display,
                    "label": f"{display}{marker}",
                    "digi": digi_label,
                    "frame": frame,
                    "lookup": lookup.get(normalize_callsign(display)) or lookup.get(normalize_callsign(station)),
                }
            )
            if len(entries) >= SCROLLER_MAX_ENTRIES:
                break
        return entries

    @staticmethod
    def _digi_label(value: str) -> str:
        normalized = normalize_callsign(value)
        if not normalized:
            return "-"
        if normalized.endswith("-0"):
            normalized = normalized[:-2]
        return f"{normalized}*"

    def _station_text_color(self, station: dict[str, Any] | None) -> QColor:
        if not station:
            return current_colors().color("text")
        latitude = parse_float(station.get("latitude"))
        longitude = parse_float(station.get("longitude"))
        if latitude is None or longitude is None:
            return current_colors().color("text")
        radius = self.map.visible_radius_km()
        if not math.isfinite(radius) or radius <= 0:
            return QColor.fromHslF(120 / 360, 1.0, 0.34)
        distance = geo.haversine_m(latitude, longitude, self.map.center_lat, self.map.center_lon) / 1000
        ratio = max(0.0, min(1.0, distance / radius))
        return QColor.fromHslF((120 * ratio) / 360, 1.0, 0.40)

    def _render_scroller(self, entries: list[dict[str, Any]]) -> None:
        rows = []
        for entry in entries:
            station = entry.get("lookup")
            frame = entry["frame"]
            icon_path = str(frame.get("display_icon_path") or (station or {}).get("symbol_icon") or "")
            overlay = ""
            if station:
                table = str(station.get("symbol_table") or "")
                overlay = table[:1] if table and table not in {"/", "\\"} else ""
            rows.append((entry, station, icon_path, overlay, self._station_text_color(station).name()))
        signature = "|".join(f"{entry['timestamp']}{entry['label']}{entry['digi']}{icon}{overlay}{color}" for entry, _station, icon, overlay, color in rows)
        if signature == getattr(self, "_scroller_signature", None) and self.scroller_list.count():
            return
        self._scroller_signature = signature
        bar = self.scroller_list.verticalScrollBar()
        follow_newest = self.scroller_list.count() <= 1 or bar.value() >= bar.maximum() - 4
        kept_position = bar.value()
        self.scroller_list.clear()
        if not rows:
            item = QListWidgetItem(t("No frames received yet."))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.scroller_list.addItem(item)
            return
        # The user's choice: the newest frame at the bottom, the list grows upwards like a terminal.
        for entry, station, icon_path, overlay, color in reversed(rows):
            item = QListWidgetItem(f"{entry['label']}    {entry['digi']}")
            item.setIcon(symbol_pixmap(icon_path, overlay, 18))
            item.setForeground(QColor(color))
            time_text = entry["timestamp"]
            item.setToolTip(f"{time_text}\n{t('Center')}: {entry['station']}" if station else time_text)
            item.setData(Qt.ItemDataRole.UserRole, station)
            self.scroller_list.addItem(item)
        self.scroller_list.doItemsLayout()
        if follow_newest:
            self.scroller_list.scrollToBottom()
        else:
            bar.setValue(kept_position)  # scrolled up to read older frames: stay there

    def _on_scroller_clicked(self, item: QListWidgetItem) -> None:
        station = item.data(Qt.ItemDataRole.UserRole)
        if not station:
            return
        latitude = parse_float(station.get("latitude"))
        longitude = parse_float(station.get("longitude"))
        if latitude is not None and longitude is not None:
            self.map.center_on(latitude, longitude, 15)

    def _render_latest(self, station: dict[str, Any] | None) -> None:
        if not station:
            self.latest_body.setText(f"<span style='color:{current_colors().css('muted')}'>{escape_html(t('No decoded APRS stations available yet.'))}</span>")
            return
        display = str(station.get("display_callsign") or station.get("callsign") or "-")
        qsy = "-"
        frequency = parse_float(station.get("qsy_frequency_mhz"))
        if frequency is not None:
            parts = [f"{frequency:.3f} MHz"]
            if station.get("qsy_tone"):
                parts.append(str(station.get("qsy_tone")))
            offset = parse_float(station.get("qsy_offset_khz"))
            if offset is not None:
                parts.append(f"{'+' if offset > 0 else ''}{int(offset)} kHz")
            if station.get("qsy_callsign"):
                parts.append(str(station.get("qsy_callsign")))
            qsy = " ".join(parts)
        distance_parts = []
        distance = parse_float(station.get("distance_km"))
        if distance is not None:
            distance_parts.append(f"{distance} km")
        own_lat = parse_float(self.map_config.get("station_latitude"))
        own_lon = parse_float(self.map_config.get("station_longitude"))
        latitude = parse_float(station.get("latitude"))
        longitude = parse_float(station.get("longitude"))
        if None not in (own_lat, own_lon, latitude, longitude):
            distance_parts.append(f"{round(geo.bearing_deg(own_lat, own_lon, latitude, longitude))}°")
        comment = str(station.get("comment") or "").strip() or "-"
        rows = [
            (t("Callsign"), f"<b>{escape_html(display)}</b>"),
            (t("QSY"), f"<b>{escape_html(qsy)}</b>"),
            (t("Distance and azimuth"), escape_html(", ".join(distance_parts) or "-")),
            (t("Comment"), escape_html(comment)),
        ]
        muted = current_colors().css("muted")
        self.latest_body.setText(
            "<table cellspacing='2'>"
            + "".join(f"<tr><td style='color:{muted}; padding-right:8px'>{escape_html(caption)}</td><td>{value}</td></tr>" for caption, value in rows)
            + "</table>"
        )

    # --------------------------------------------------------------- overlays
    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt API
        from PySide6.QtCore import QEvent

        if watched is self.map and event.type() == QEvent.Type.Resize:
            self._position_overlays()
        elif watched is self.map and event.type() == QEvent.Type.MouseMove:
            if not self.top_bar_shown and event.position().y() <= TOP_BAR_REVEAL_PX:
                self._set_top_bar_shown(True)
        elif watched is self.body_widget and event.type() == QEvent.Type.MouseMove:
            # The strip above the map (the page margin) belongs to the top edge too.
            if not self.top_bar_shown and event.position().y() <= self.map.y() + TOP_BAR_REVEAL_PX:
                self._set_top_bar_shown(True)
        elif watched is self.body_widget and event.type() == QEvent.Type.Resize:
            self._position_top_bar()
        elif (event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide)
              and any(watched is widget for widget in getattr(self, "_caption_watch", ()))):
            self._place_toolbar_captions()
        return super().eventFilter(watched, event)

    def _place_toolbar_captions(self) -> None:
        """Each caption centred under its list, on the info line; hidden with its list (the RF paths
        one goes with the paths) and where the info texts on the left (tiles, centre, zoom) or the
        paths legend on the right would reach it."""
        # Showing a caption makes Qt lay the bar out again, which moves the watched widgets and calls
        # this again from inside show(): run once more afterwards instead (a hide nested in the show
        # leaves the caption flagged hidden and visible at once, and it never hides after that).
        if self._placing_captions:
            self._captions_dirty = True
            return
        self._placing_captions = True
        try:
            for _attempt in range(3):
                self._captions_dirty = False
                left_limit = self.zoom_label.geometry().right() + 12
                legend = self.paths_legend
                right_limit = legend.x() - 12 if legend.isVisible() and legend.text() else self.top_bar.width()
                top = self.zoom_label.y()
                for combo, caption in self.toolbar_captions:
                    size = caption.sizeHint()
                    origin = combo.mapTo(self.top_bar, QPoint(0, 0))
                    x = origin.x() + (combo.width() - size.width()) // 2
                    caption.setGeometry(x, top, size.width(), size.height())
                    caption.setVisible(combo.isVisibleTo(self.top_bar) and left_limit <= x
                                       and x + size.width() <= right_limit)
                if not self._captions_dirty:
                    break
        finally:
            self._placing_captions = False

    # --------------------------------------------------------------- top bar
    def _top_bar_target(self, shown: bool) -> QPoint:
        width = self.body_widget.width()
        self._fit_toolbar_row(width)
        layout = self.top_bar.layout()
        layout.activate()
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else -1
        height = max(height, self.top_bar.sizeHint().height())
        self.top_bar.resize(width, height)
        return QPoint(0, 0 if shown else -height - 2)

    def _position_top_bar(self) -> None:
        target = self._top_bar_target(self.top_bar_shown)
        if self._top_bar_animation.state() == QAbstractAnimation.State.Running:
            self._top_bar_animation.setEndValue(target)
        else:
            self.top_bar.move(target)
        self.top_bar.raise_()

    def _set_top_bar_shown(self, shown: bool, *, animate: bool = True) -> None:
        if shown:
            self._top_bar_hide_timer.start()  # it slides back unless the mouse stays on it
        else:
            self._top_bar_hide_timer.stop()
        if shown == self.top_bar_shown:
            return
        self.top_bar_shown = shown
        target = self._top_bar_target(shown)
        self.top_bar.raise_()
        self._top_bar_animation.stop()
        if not animate:
            self.top_bar.move(target)
            return
        self._top_bar_animation.setEasingCurve(QEasingCurve.Type.OutCubic if shown else QEasingCurve.Type.InCubic)
        self._top_bar_animation.setStartValue(self.top_bar.pos())
        self._top_bar_animation.setEndValue(target)
        self._top_bar_animation.start()

    def _top_bar_in_use(self, cursor: QPoint) -> bool:
        """The mouse is on the bar, or one of its drop-down lists is open."""
        if QApplication.activePopupWidget() is not None:
            return True
        point = self.body_widget.mapFromGlobal(cursor)
        return 0 <= point.x() < self.body_widget.width() and 0 <= point.y() <= self.top_bar.height()

    def _hide_top_bar_when_idle(self, cursor: QPoint | None = None) -> None:
        if not self.top_bar_shown:
            return
        if self._top_bar_in_use(cursor if cursor is not None else QCursor.pos()):
            self._top_bar_hide_timer.start()
            return
        self._set_top_bar_shown(False)

    @staticmethod
    def _alarm_panel_width(map_width: int) -> int:
        return min(320, max(220, map_width // 3))

    @staticmethod
    def _right_panels_width(map_width: int) -> int:
        return min(320, max(220, map_width // 4))

    def _visible_map_center(self) -> QPointF | None:
        """Middle of the map part the overlay panels leave uncovered (the station list sits on the right)."""
        width, height = self.map.width(), self.map.height()
        left, right = 0, width
        if self.latest_visible or self.scroller_visible:
            right = width - self._right_panels_width(width) - 10
        if self.alarm_panel_open:
            left = 10 + self._alarm_panel_width(width)
        if right - left < 200:
            return None  # not laid out yet, or too narrow to bother
        return QPointF((left + right) / 2, height / 2)

    def _position_overlays(self) -> None:
        width, height = self.map.width(), self.map.height()
        top = self.map.zoom_control_bottom() + 10
        self.alarm_panel.setGeometry(10, top, self._alarm_panel_width(width), max(120, min(height - top - 30, 520)))
        right_width = self._right_panels_width(width)
        y = 10
        if self.latest_visible:
            layout = self.latest_panel.layout()
            layout.activate()
            hinted = layout.heightForWidth(right_width) if layout.hasHeightForWidth() else -1
            latest_height = min(max(hinted, self.latest_panel.sizeHint().height()), 240)
            self.latest_panel.setGeometry(width - right_width - 10, y, right_width, latest_height)
            y += latest_height + 8
        scroller_height = max(120, height - y - 34)
        self.scroller_panel.setGeometry(width - right_width - 10, y, right_width, scroller_height)
        self.alarm_panel.setVisible(self.alarm_panel_open)
        self.latest_panel.setVisible(self.latest_visible)
        self.scroller_panel.setVisible(self.scroller_visible)
        self.alarm_panel.raise_()
        self.latest_panel.raise_()
        self.scroller_panel.raise_()
        self._position_top_bar()

    # ----------------------------------------------------------------- toggles
    def _apply_toggle_states(self) -> None:
        self.tracks_button.setIcon(icon("track-light" if self.tracks_visible else "track-light-off", None, 18))
        self.tracks_button.setToolTip(t("Hide tracks") if self.tracks_visible else t("Show tracks"))
        self.coverage_button.setIcon(icon("map-marker-radius" if self.coverage_visible else "map-marker-radius-outline", None, 18))
        self.coverage_button.setToolTip(t("Hide coverage") if self.coverage_visible else t("Show coverage"))
        self.latest_button.setIcon(icon("message-alert" if self.latest_visible else "message-off", None, 18))
        self.latest_button.setToolTip(t("Hide latest packet widget") if self.latest_visible else t("Show latest packet widget"))
        self.scroller_button.setIcon(icon("format-list-bulleted" if self.scroller_visible else "format-list-bulleted-square", None, 18))
        self.scroller_button.setToolTip(f"{t('Hide') if self.scroller_visible else t('Show')} {t('Traffic Monitor')}")
        self.ruler_button.setIcon(icon("ruler" if self.ruler_visible else "ruler-square", None, 18))
        self.ruler_button.setToolTip(t("Hide ruler") if self.ruler_visible else t("Show ruler"))
        self.grid_button.setIcon(icon("grid" if self.grid_visible else "grid-off", None, 18))
        self.grid_button.setToolTip(t("Hide Maidenhead grid") if self.grid_visible else t("Show Maidenhead grid"))
        self.paths_button.setIcon(icon("transit-connection-variant" if self.paths_visible else "transit-connection-horizontal", None, 18))
        self.paths_button.setToolTip(t("Hide RF paths") if self.paths_visible else t("Show RF paths"))
        self.paths_button.setCheckable(True)
        self.paths_button.setChecked(self.paths_visible)
        for widget in (self.paths_window_combo, self.paths_rf_check, self.paths_is_check):
            widget.setVisible(self.paths_visible)
        self.map.set_ruler_visible(self.ruler_visible)
        self.map.set_grid_visible(self.grid_visible)
        self._sync_alarm_button(list((self.payload.get("alert_areas") or {}).get("alerts") or []))
        self._position_overlays()

    def _toggle_tracks(self) -> None:
        self.tracks_visible = not self.tracks_visible
        self.context.set_ui_value("map/tracks_visible", self.tracks_visible)
        self._apply_toggle_states()
        self._render_all()

    def _toggle_coverage(self) -> None:
        self.coverage_visible = not self.coverage_visible
        self.context.set_ui_value("map/coverage_visible", self.coverage_visible)
        self._apply_toggle_states()
        self._render_all()

    def _toggle_alarm_panel(self) -> None:
        self._set_alarm_panel_open(not self.alarm_panel_open)

    def _set_alarm_panel_open(self, open_panel: bool) -> None:
        self.alarm_panel_open = bool(open_panel)
        self.context.set_ui_value("map/alerts_overlay_open", self.alarm_panel_open)
        self._apply_toggle_states()
        self.map.set_layer("areas", alert_area_shapes(self.payload.get("alert_areas") or {}, self.hidden_alert_ids) if self.alarm_panel_open else [])

    def _toggle_latest(self) -> None:
        self.latest_visible = not self.latest_visible
        self.context.set_ui_value("map/latest_overlay_visible", self.latest_visible)
        self._apply_toggle_states()

    def _toggle_scroller(self) -> None:
        self.scroller_visible = not self.scroller_visible
        self.context.set_ui_value("map/scroller_visible", self.scroller_visible)
        self._apply_toggle_states()

    def _toggle_ruler(self) -> None:
        self.ruler_visible = not self.ruler_visible
        self.context.set_ui_value("map/ruler_visible", self.ruler_visible)
        self._apply_toggle_states()

    def _toggle_grid(self) -> None:
        self.grid_visible = not self.grid_visible
        self.context.set_ui_value("map/maidenhead_grid_visible", self.grid_visible)
        self._apply_toggle_states()

    def _toggle_paths(self) -> None:
        self.paths_visible = not self.paths_visible
        self.context.set_ui_value("map/rf_paths_visible", self.paths_visible)
        self._apply_toggle_states()
        if not self.paths_visible:
            self.routes = []
            self._routes_signature = None
            self._render_paths()
        self._sync_routes_timer()

    def _objects_window_changed(self) -> None:
        seconds = self.objects_window_combo.currentData()
        if seconds is None:
            return
        self.objects_window = int(seconds)
        self.context.set_ui_value("map/object_window_seconds", self.objects_window)
        if getattr(self, "payload", None) is not None:
            self._render_all()

    def _history_window_changed(self) -> None:
        seconds = self.history_window_combo.currentData()
        if seconds is None:
            return
        self.history_window = int(seconds)
        self.context.set_ui_value("map/track_window_seconds", self.history_window)
        if getattr(self, "payload", None) is not None:
            self._render_all()

    def _paths_window_changed(self) -> None:
        seconds = self.paths_window_combo.currentData()
        if seconds is None:
            return
        self.paths_window = int(seconds)
        self.context.set_ui_value("map/rf_paths_window_seconds", self.paths_window)
        self._sync_routes_timer()

    def _paths_filter_changed(self) -> None:
        self.context.set_ui_value("map/rf_paths_rf", self.paths_rf_check.isChecked())
        self.context.set_ui_value("map/rf_paths_aprsis", self.paths_is_check.isChecked())
        if getattr(self, "payload", None) is not None:
            self._render_paths()

    def _mask_key(self) -> str:
        return mask_setting_key(self.map.tiles.source.source_id, dark=current_colors().is_dark)

    def _apply_mask_from_settings(self) -> None:
        value = stored_mask_percent(self.context, self.map.tiles.source.source_id, dark=current_colors().is_dark)
        self.mask_combo.blockSignals(True)
        self.mask_combo.setCurrentIndex(self.mask_combo.findData(value))
        self.mask_combo.blockSignals(False)
        self.map.set_mask(*map_mask(value))

    def _mask_changed(self) -> None:
        value = self.mask_combo.currentData()
        if value is None:
            return
        self.context.set_ui_value(self._mask_key(), int(value))
        self.map.set_mask(*map_mask(int(value)))

    # ------------------------------------------------------------------- view
    def _reset_view(self, persist: bool = True) -> None:
        latitude = parse_float(self.map_config.get("station_latitude"))
        longitude = parse_float(self.map_config.get("station_longitude"))
        zoom = STATION_VIEW_ZOOM
        if persist:
            self.context.set_ui_value("map/view", "")
        if latitude is None or longitude is None:
            latitude, longitude, zoom = 52.0, 19.0, 6
        self.map.set_view_at(latitude, longitude, zoom, anchor=self._visible_map_center())

    def _on_view_changed(self, latitude: float, longitude: float, zoom: int) -> None:
        self.center_label.setText(f"{t('Center')}: {latitude:.5f}, {longitude:.5f}")
        self.zoom_label.setText(f"{t('Zoom')}: {zoom}")
        if self._view_initialized:
            self.context.set_ui_value("map/view", json.dumps({"latitude": latitude, "longitude": longitude, "zoom": zoom}))
        self._render_scroller_and_latest()

    def _on_tile_status(self, status: str) -> None:
        if status == "unavailable":
            self._update_tile_label(t("Tile provider unavailable"))
        elif status == "recovered":
            self._update_tile_label(t("Tile provider recovered"))
            QTimer.singleShot(8000, lambda: self._update_tile_label(""))

    def _fill_tiles_combo(self) -> None:
        from app.services.map_service import list_map_sources

        try:
            sources = [item for item in list_map_sources() if item.get("enabled")]
        except Exception:
            sources = []
        current = self.map.tiles.source.source_id
        self.tiles_combo.blockSignals(True)
        self.tiles_combo.clear()
        for item in sources:
            self.tiles_combo.addItem(str(item.get("name") or item.get("id")), int(item["id"]))
        self.tiles_combo.setCurrentIndex(max(0, self.tiles_combo.findData(current)))
        self.tiles_combo.setVisible(len(sources) > 1)  # nothing to pick from a single source
        self.tiles_combo.blockSignals(False)

    def _tiles_source_changed(self) -> None:
        source_id = self.tiles_combo.currentData()
        if source_id is None:
            return
        self.context.set_ui_value("map/tile_source_id", int(source_id))
        self.map.tiles.set_preferred_source(int(source_id))  # every map in the window switches with it
        self._apply_mask_from_settings()  # each map background keeps its own darkening / lightening
        self._update_tile_label("")
        self._place_toolbar_captions()

    def _update_tile_label(self, status: str) -> None:
        name = self.map.tiles.source.name or "-"
        self.tile_label.setText(f"{t('Tiles')}: {name}" + (f" ({status})" if status else ""))

    def _open_station(self, payload: Any) -> None:
        if isinstance(payload, dict):
            if payload.get("origin") == OWN_SETTINGS_ORIGIN:
                # No frames of it yet, so there is no station detail: open its settings instead.
                self.navigate("station")
                return
            callsign = payload.get("display_callsign") or payload.get("callsign")
            if callsign:
                self.navigate("station-detail", str(callsign))

    def center_on(self, latitude: float, longitude: float) -> None:
        self.ensure_built()
        self._view_initialized = True
        self.map.center_on(latitude, longitude, 15, anchor=self._visible_map_center())
