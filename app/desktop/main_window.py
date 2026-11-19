"""Main window: sidebar navigation (as in the web GUI) and the page stack."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app import get_version
from app.db import log_event
from app.desktop import context as ctx
from app.desktop.i18n import status_text, t, tf
from app.desktop.icons import app_icon, icon, icon_pixmap
from app.desktop.logo import logo_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors
from app.desktop.widgets.common import Page, confirm, icon_button, label


@dataclass(frozen=True)
class NavEntry:
    key: str
    label: str
    icon: str
    module: str
    cls: str
    roles: tuple[str, ...] = ("admin", "operator", "viewer")
    visible_roles: tuple[str, ...] = ()
    separator: bool = False


def _sep(key: str) -> NavEntry:
    return NavEntry(key=key, label="", icon="", module="", cls="", roles=("admin", "operator"), visible_roles=("viewer",), separator=True)


# Same order, labels, icons and role rules as app/template_helpers.PRIMARY_NAV.
NAVIGATION: tuple[NavEntry, ...] = (
    NavEntry("dashboard", "Dashboard", "view-dashboard-outline", "app.desktop.pages.dashboard", "DashboardPage"),
    NavEntry("map", "Map", "map-outline", "app.desktop.pages.map_page", "MapPage"),
    NavEntry("stations", "Stations", "account-multiple", "app.desktop.pages.stations", "StationsPage"),
    NavEntry("traffic", "Traffic Monitor", "radio-tower", "app.desktop.pages.traffic", "TrafficPage"),
    NavEntry("alerts", "Alerts", "alarm-light-outline", "app.desktop.pages.alerts", "AlertsPage"),
    NavEntry("band-condition", "Band Condition", "chart-line", "app.desktop.pages.band_condition", "BandConditionPage"),
    NavEntry("statistics", "Statistics", "chart-bar-stacked", "app.desktop.pages.statistics", "StatisticsPage"),
    _sep("nav-separator-primary"),
    NavEntry("modems", "Interfaces", "radio-handheld", "app.desktop.pages.interfaces", "InterfacesPage"),
    NavEntry("station", "My Station", "antenna", "app.desktop.pages.station", "StationPage", ("admin", "operator"), ("viewer",)),
    NavEntry("wx", "WX", "weather-partly-snowy", "app.desktop.pages.wx", "WxPage", ("admin", "operator"), ("viewer",)),
    NavEntry("messages", "Messages", "message-reply-text-outline", "app.desktop.pages.messages", "MessagesPage", ("admin", "operator"), ("viewer",)),
    NavEntry("notifications", "Notifications", "bell-outline", "app.desktop.pages.notifications", "NotificationsPage", ("admin", "operator"), ("viewer",)),
    NavEntry("objects", "Objects / Items", "crosshairs", "app.desktop.pages.objects", "ObjectsPage", ("admin", "operator"), ("viewer",)),
    NavEntry("bulletins", "Bulletins", "message-text-outline", "app.desktop.pages.bulletins", "BulletinsPage", ("admin", "operator"), ("viewer",)),
    NavEntry("digi-flows", "Packet Routing", "source-branch-check", "app.desktop.pages.digi_flows", "DigiFlowsPage", ("admin", "operator"), ("viewer",)),
    _sep("nav-separator-secondary"),
    NavEntry("logs", "Logs", "book-open-variant", "app.desktop.pages.logs", "LogsPage", ("admin", "operator"), ("viewer",)),
    NavEntry("users", "Users / Roles", "account-cog", "app.desktop.pages.users", "UsersPage", ("admin",), ("viewer",)),
    NavEntry("settings", "Settings", "cog", "app.desktop.pages.settings", "SettingsPage", ("admin", "operator"), ("viewer",)),
    NavEntry("changelog", "Changelog", "language-markdown-outline", "app.desktop.pages.changelog", "ChangelogPage", ("admin", "operator"), ("viewer",)),
)

# Pages reached from other pages (not in the sidebar); value = (module, class, highlighted nav key).
SUBPAGES: dict[str, tuple[str, str, str]] = {
    "station-detail": ("app.desktop.pages.station_detail", "StationDetailPage", "stations"),
    "alert-detail": ("app.desktop.pages.alert_detail", "AlertDetailPage", "alerts"),
    "alert-send": ("app.desktop.pages.alert_send", "AlertSendPage", "alerts"),
    "frame-detail": ("app.desktop.pages.frame_detail", "FrameDetailPage", "traffic"),
    "digi-flow-edit": ("app.desktop.pages.digi_flow_editor", "DigiFlowEditorPage", "digi-flows"),
}

NAV_KEY_ROLE = Qt.ItemDataRole.UserRole
NAV_BADGE_ROLE = Qt.ItemDataRole.UserRole + 1
NAV_SEPARATOR_ROLE = Qt.ItemDataRole.UserRole + 2
NAV_ALERT_ROLE = Qt.ItemDataRole.UserRole + 3


class NavDelegate(QStyledItemDelegate):
    """Nav rows with an icon, label and the web GUI's count badge."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.collapsed = False

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802 - Qt API
        if index.data(NAV_SEPARATOR_ROLE):
            return QSize(option.rect.width(), 13)
        return QSize(option.rect.width(), 36)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        colors = current_colors()
        if index.data(NAV_SEPARATOR_ROLE):
            painter.save()
            painter.setPen(QPen(colors.color("border"), 1))
            y = option.rect.center().y()
            painter.drawLine(option.rect.left() + 8, y, option.rect.right() - 8, y)
            painter.restore()
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        opt.icon = QIcon()
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        attention = bool(index.data(NAV_ALERT_ROLE))
        text_color = colors.color("text") if enabled else colors.color("muted")
        if attention and enabled:
            text_color = colors.color("warning")
        icon_name = str(index.data(Qt.ItemDataRole.DecorationRole + 100) or "")
        rect = option.rect.adjusted(10, 0, -8, 0)
        icon_size = 20
        icon_rect = QRect(rect.left(), rect.center().y() - icon_size // 2, icon_size, icon_size)
        if icon_name:
            painter.drawPixmap(icon_rect, icon_pixmap(icon_name, text_color if enabled else colors.color("muted"), icon_size))
        badge = index.data(NAV_BADGE_ROLE)
        if not self.collapsed:
            text_rect = QRect(icon_rect.right() + 10, rect.top(), rect.width() - icon_size - 14, rect.height())
            font = QFont(opt.font)
            if attention:
                font.setBold(True)
            painter.setFont(font)
            painter.setPen(text_color)
            badge_width = 0
            if badge:
                badge_width = 30
            painter.drawText(text_rect.adjusted(0, 0, -badge_width, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if badge:
            badge_text = str(badge)
            font = QFont(opt.font)
            font.setBold(True)
            font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
            painter.setFont(font)
            metrics = painter.fontMetrics()
            width = max(18, metrics.horizontalAdvance(badge_text) + 10)
            if self.collapsed:
                badge_rect = QRect(icon_rect.right() - 6, icon_rect.top() - 6, width, 16)
            else:
                badge_rect = QRect(rect.right() - width, rect.center().y() - 9, width, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.color("danger") if attention else colors.color("accent"))
            painter.drawRoundedRect(badge_rect, 9, 9)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
        painter.restore()


class Sidebar(QWidget):
    navigate = Signal(str)
    send_beacon_clicked = Signal()
    theme_toggle_clicked = Signal()
    logout_clicked = Signal()
    collapse_toggled = Signal(bool)

    EXPANDED_WIDTH = 248
    COLLAPSED_WIDTH = 66

    def __init__(self, context: ctx.AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.collapsed = str(context.ui_value("sidebar_state", "expanded")) == "collapsed"
        self.clock_mode = "local" if str(context.ui_value("clock_mode", "utc")) == "local" else "utc"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(10)

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo.mousePressEvent = lambda _event: self.navigate.emit("dashboard")  # type: ignore[method-assign]
        layout.addWidget(self.logo)

        self.user_box = QFrame()
        self.user_box.setObjectName("UserBox")
        user_layout = QHBoxLayout(self.user_box)
        user_layout.setContentsMargins(8, 6, 8, 6)
        user_layout.setSpacing(6)
        self.role_icon = QLabel()
        self.username = label("", None)
        self.username.setObjectName("SidebarUsername")
        user_layout.addWidget(self.role_icon)
        user_layout.addWidget(self.username, 1)
        self.beacon_button = icon_button("satellite-uplink", t("Send beacon"), self.send_beacon_clicked.emit)
        self.theme_button = icon_button("theme-light-dark", t("Switch theme"), self.theme_toggle_clicked.emit)
        self.logout_button = icon_button("logout-variant", t("Logout"), self.logout_clicked.emit)
        for widget in (self.beacon_button, self.theme_button, self.logout_button):
            user_layout.addWidget(widget)
        layout.addWidget(self.user_box)

        self.clock = QLabel()
        self.clock.setObjectName("UtcClock")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clock.setToolTip(t("Click to switch between UTC and local time"))
        self.clock.mousePressEvent = lambda _event: self._toggle_clock()  # type: ignore[method-assign]
        layout.addWidget(self.clock)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setMouseTracking(True)
        self.delegate = NavDelegate(self.nav)
        self.nav.setItemDelegate(self.delegate)
        self.nav.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.nav, 1)

        self.footer = label("", None)
        self.footer.setObjectName("SidebarFooter")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)

        self.collapse_button = icon_button("chevron-double-left", t("Collapse sidebar"), self._toggle_collapse, size=16)
        layout.addWidget(self.collapse_button, 0, Qt.AlignmentFlag.AlignCenter)

        self._items: dict[str, QListWidgetItem] = {}
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self.rebuild()

    # -- content ---------------------------------------------------------------
    def rebuild(self, *, hidden_keys: set[str] | None = None, selected: str | None = None) -> None:
        hidden_keys = hidden_keys or set()
        self.nav.clear()
        self._items = {}
        role = self.context.user.role
        for entry in NAVIGATION:
            if entry.key in hidden_keys:
                continue
            if role not in entry.roles and role not in entry.visible_roles:
                continue
            item = QListWidgetItem()
            if entry.separator:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setData(NAV_SEPARATOR_ROLE, True)
                item.setSizeHint(QSize(10, 13))
                self.nav.addItem(item)
                continue
            text = t(entry.label)
            item.setText(text)
            item.setData(NAV_KEY_ROLE, entry.key)
            item.setData(Qt.ItemDataRole.DecorationRole + 100, entry.icon)
            disabled = role not in entry.roles
            if disabled:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setToolTip(f"{text} - {t('Access not available for your role')}")
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(text)
            self.nav.addItem(item)
            self._items[entry.key] = item
        if selected:
            self.select(selected)
        self.refresh_chrome()

    def refresh_chrome(self) -> None:
        colors = current_colors()
        user = self.context.user
        role_icons = {"admin": "shield-crown-outline", "operator": "shield-account-outline"}
        self.role_icon.setPixmap(icon_pixmap(role_icons.get(user.role, "eye-circle-outline"), colors.color("accent"), 18))
        self.role_icon.setToolTip(user.role)
        self.username.setText(user.username)
        self.beacon_button.setVisible(user.role in {"admin", "operator"})
        self.logout_button.setVisible(ctx.require_login())
        is_light = not colors.is_dark
        self.theme_button.setIcon(icon("weather-night" if is_light else "white-balance-sunny", None, 18))
        self.theme_button.setToolTip(t("Switch to night mode") if is_light else t("Switch to day mode"))
        self.beacon_button.setIcon(icon("satellite-uplink", None, 18))
        self.logout_button.setIcon(icon("logout-variant", None, 18))
        self.footer.setText(f"{t('Version')} {get_version()}\n{t('Copyright SQ9MDD')}")
        self._apply_collapsed()
        self._update_clock()

    def _apply_collapsed(self) -> None:
        colors = current_colors()
        self.delegate.collapsed = self.collapsed
        width = self.COLLAPSED_WIDTH if self.collapsed else self.EXPANDED_WIDTH
        self.setFixedWidth(width)
        if self.collapsed:
            self.logo.setPixmap(QIcon(app_icon()).pixmap(36, 36))
        else:
            self.logo.setPixmap(logo_pixmap(colors, self.EXPANDED_WIDTH - 40))
        self.user_box.setVisible(not self.collapsed)
        self.footer.setVisible(not self.collapsed)
        self.collapse_button.setIcon(icon("chevron-double-right" if self.collapsed else "chevron-double-left", None, 16))
        self.collapse_button.setToolTip(t("Expand sidebar") if self.collapsed else t("Collapse sidebar"))
        self.nav.viewport().update()

    def _toggle_collapse(self) -> None:
        self.collapsed = not self.collapsed
        self.context.set_ui_value("sidebar_state", "collapsed" if self.collapsed else "expanded")
        self._apply_collapsed()
        self._update_clock()
        self.collapse_toggled.emit(self.collapsed)

    def _toggle_clock(self) -> None:
        self.clock_mode = "local" if self.clock_mode == "utc" else "utc"
        self.context.set_ui_value("clock_mode", self.clock_mode)
        self._update_clock()

    def _update_clock(self) -> None:
        now = datetime.now(timezone.utc)
        if self.clock_mode == "local":
            now = now.astimezone()
            zone = t("Local")
        else:
            zone = t("UTC")
        if self.collapsed:
            self.clock.setText(now.strftime("%H:%M"))
        else:
            self.clock.setText(f"{now.strftime('%H:%M:%S')}\n{now.strftime('%Y.%m.%d')}  {zone}")

    def select(self, key: str) -> None:
        item = self._items.get(key)
        self.nav.blockSignals(True)
        if item is not None:
            self.nav.setCurrentItem(item)
        else:
            self.nav.clearSelection()
        self.nav.blockSignals(False)

    def set_badge(self, key: str, count: int, *, attention: bool = False, icon_name: str | None = None) -> None:
        item = self._items.get(key)
        if item is None:
            return
        item.setData(NAV_BADGE_ROLE, int(count) if count else None)
        item.setData(NAV_ALERT_ROLE, bool(attention))
        if icon_name:
            item.setData(Qt.ItemDataRole.DecorationRole + 100, icon_name)
        self.nav.viewport().update()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(NAV_KEY_ROLE)
        if key:
            self.navigate.emit(str(key))


class _UnavailablePage(Page):
    def build(self) -> None:
        self.body.addWidget(label(t("This page could not be loaded."), "page-title"))
        self.body.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, context: ctx.AppContext) -> None:
        super().__init__()
        self.context = context
        context.main_window = self
        context.navigate = self.navigate
        self._program_icon_key: tuple | None = None
        self._update_program_icon(*self._initial_icon_state())
        self.setMinimumSize(980, 640)
        self.resize(1440, 900)

        central = QWidget()
        central.setObjectName("PageHost")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = Sidebar(context)
        self.sidebar.navigate.connect(lambda key: self.navigate(key))
        self.sidebar.send_beacon_clicked.connect(self._send_beacon)
        self.sidebar.theme_toggle_clicked.connect(self._toggle_theme)
        self.sidebar.logout_clicked.connect(self._logout)
        root.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Pages")
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_runtime = QLabel()
        self.status_interfaces = QLabel()
        self.status_aprsis = QLabel()
        self.status.addWidget(self.status_runtime)
        self.status.addWidget(self.status_interfaces, 1)
        self.status.addPermanentWidget(self.status_aprsis)

        self.pages: dict[str, Page] = {}
        self.current_key = ""
        self._beacon_cooldown_until = 0.0
        self._hidden_nav: set[str] = set()
        self._really_quit = False

        self.tray: QSystemTrayIcon | None = None
        self._setup_tray()

        self._chrome_loader = SerialLoader(self._load_chrome_state, self._apply_chrome_state, self)
        self._chrome_timer = QTimer(self)
        self._chrome_timer.timeout.connect(self._chrome_loader.request)
        self._chrome_timer.start(5000)

        from app.desktop.alerts_watcher import AlertWatcher

        self.alert_watcher = AlertWatcher(context, self)

        context.signals.settings_changed.connect(self._on_settings_changed)
        context.signals.data_changed.connect(lambda _area: self._chrome_loader.request())
        application = QApplication.instance()
        if application is not None and hasattr(application, "commitDataRequest"):
            application.commitDataRequest.connect(self._on_session_end)
        self._update_title()
        self._chrome_loader.request()

    # -- navigation -------------------------------------------------------------
    def navigate(self, key: str, argument: Any = None) -> None:
        page = self._page(key)
        if page is None:
            return
        previous = self.pages.get(self.current_key)
        if previous is not None and previous is not page:
            previous.deactivate()
        self.current_key = key
        self.stack.setCurrentWidget(page)
        highlighted = SUBPAGES[key][2] if key in SUBPAGES else key
        self.sidebar.select(highlighted)
        try:
            page.activate(argument)
        except Exception as exc:  # pragma: no cover - defensive, keeps the window alive
            log_event("ERROR", "desktop", f"Opening page {key} failed: {exc}")
            page.flash.error(str(exc))
        self._update_title()

    def _page(self, key: str) -> Page | None:
        if key in self.pages:
            return self.pages[key]
        spec: tuple[str, str] | None = None
        for entry in NAVIGATION:
            if entry.key == key and not entry.separator:
                if self.context.user.role not in entry.roles:
                    return None
                spec = (entry.module, entry.cls)
                break
        if spec is None and key in SUBPAGES:
            spec = SUBPAGES[key][:2]
        if spec is None:
            return None
        try:
            module = importlib.import_module(spec[0])
            page_class = getattr(module, spec[1])
        except (ModuleNotFoundError, AttributeError) as exc:
            log_event("ERROR", "desktop", f"Page {key} is not available: {exc}")
            page_class = _UnavailablePage
        page = page_class(self.context)
        page.navigate_requested.connect(self.navigate)
        self.pages[key] = page
        self.stack.addWidget(page)
        return page

    def current_page(self) -> Page | None:
        return self.pages.get(self.current_key)

    # -- chrome state (title, badges, status bar) -------------------------------------
    def _load_chrome_state(self) -> dict[str, Any]:
        from app.db import connection_scope
        from app.services.alarm_groups import get_aprs_alarm_enabled
        from app.services.alerts import attention_alert_count
        from app.services.aprsis import get_aprsis_runtime_status
        from app.services.band_condition import is_band_condition_enabled
        from app.services.content import get_aprs_symbol_set, get_station_settings, traffic_snapshot
        from app.services.messages import get_unread_inbox_count

        with connection_scope():
            alarm_enabled = get_aprs_alarm_enabled()
            traffic = traffic_snapshot(limit=0)
            traffic["_aprsis"] = get_aprsis_runtime_status()
            state = {
                "unread": get_unread_inbox_count(),
                "alarm_enabled": alarm_enabled,
                "alert_count": attention_alert_count(expire=False, alarm_enabled=alarm_enabled) if alarm_enabled else 0,
                "band_enabled": is_band_condition_enabled(),
                "station": get_station_settings(include_tx_runtime=False),
                "symbol_set": get_aprs_symbol_set(),
                "station_symbol_icon": ctx.station_symbol_icon_enabled(),
                "traffic": traffic,
            }
        return state

    def _apply_chrome_state(self, state: dict[str, Any]) -> None:
        hidden = set()
        if not state.get("band_enabled"):
            hidden.add("band-condition")
        if not state.get("alarm_enabled"):
            hidden.add("alerts")
        if hidden != self._hidden_nav:
            self._hidden_nav = hidden
            highlighted = SUBPAGES[self.current_key][2] if self.current_key in SUBPAGES else self.current_key
            self.sidebar.rebuild(hidden_keys=hidden, selected=highlighted)
        unread = int(state.get("unread") or 0)
        self.sidebar.set_badge(
            "messages",
            unread,
            attention=unread > 0,
            icon_name="message-alert-outline" if unread > 0 else "message-reply-text-outline",
        )
        alerts = int(state.get("alert_count") or 0)
        self.sidebar.set_badge("alerts", alerts, attention=alerts > 0)
        self._station_settings = state.get("station") or {}
        self._update_program_icon(self._station_settings, state.get("symbol_set"), bool(state.get("station_symbol_icon", True)))
        self._update_title()
        self._update_status(state.get("traffic") or {})
        if self.tray is not None:
            tooltip = f"APRSBox: {self._station_identity()}"
            if unread:
                tooltip += "\n" + tf("{count} unread messages", count=unread)
            self.tray.setToolTip(tooltip)

    # -- program icon (the station's APRS symbol, like APRSIS32) ---------------------
    @staticmethod
    def _initial_icon_state() -> tuple[dict[str, Any], str | None, bool]:
        try:
            from app.services.content import get_aprs_symbol_set, get_station_settings

            return get_station_settings(include_tx_runtime=False), get_aprs_symbol_set(), ctx.station_symbol_icon_enabled()
        except Exception:
            return {}, None, True

    def _update_program_icon(self, station: dict[str, Any] | None, symbol_set: str | None, enabled: bool) -> None:
        from app.desktop.icons import program_icon, program_icon_key

        key = program_icon_key(station, symbol_set=symbol_set, use_station_symbol=enabled)
        if key == self._program_icon_key:
            return
        self._program_icon_key = key
        icon = program_icon(station, symbol_set=symbol_set, use_station_symbol=enabled)
        self.setWindowIcon(icon)
        application = QApplication.instance()
        if application is not None:
            application.setWindowIcon(icon)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.setIcon(icon)

    def _station_identity(self) -> str:
        station = getattr(self, "_station_settings", None) or {}
        callsign = str(station.get("callsign") or "").strip().upper() or "N0CALL"
        ssid = str(station.get("ssid") or "").strip()
        return f"{callsign}-{ssid}" if ssid and ssid != "0" and ssid.isdigit() else callsign

    def _update_title(self) -> None:
        self.setWindowTitle(f"APRSBox: {self._station_identity()}")

    def _update_status(self, traffic: dict[str, Any]) -> None:
        runtime = self.context.runtime
        if runtime is not None and runtime.running:
            self.status_runtime.setText(t("APRS runtime: running"))
        else:
            self.status_runtime.setText(t("APRS runtime: stopped"))
        interfaces = list(traffic.get("interfaces") or [])
        parts = []
        for item in interfaces:
            name = str(item.get("name") or "-")
            status = str(item.get("status") or "-")
            parts.append(f"{name}: {status_text(status)}")
        self.status_interfaces.setText(" · ".join(parts) if parts else t("No enabled interface is configured."))
        aprsis = traffic.get("_aprsis") or {}
        status = str(aprsis.get("status") or "")
        self.status_aprsis.setText(f"APRS-IS: {status_text(status)}" if status else "")

    # -- actions ------------------------------------------------------------------
    def _send_beacon(self) -> None:
        import time

        from app.desktop.widgets.common import error, info

        remaining = self._beacon_cooldown_until - time.monotonic()
        if remaining > 0:
            return
        if not confirm(self, t("Send beacon"), t("Are you sure you want to send the beacon now?"), confirm_text=t("Send beacon")):
            return
        self._beacon_cooldown_until = time.monotonic() + 10.0
        self.sidebar.beacon_button.setEnabled(False)
        QTimer.singleShot(10_000, lambda: self.sidebar.beacon_button.setEnabled(True))
        from app.services.content import get_station_settings
        from app.services.outbound import enqueue_beacon_job

        try:
            success, message = enqueue_beacon_job(get_station_settings())
        except Exception as exc:  # pragma: no cover - defensive
            success, message = False, str(exc)
        if success:
            info(self, t("Send beacon"), t(message or "Beacon queued for transmission."))
        else:
            error(self, t("Send beacon"), t(message or "Beacon could not be queued."))
        self.context.notify_data_changed("station")

    def _toggle_theme(self) -> None:
        from app.desktop.app import apply_current_theme

        mode = "light" if current_colors().is_dark else "dark"
        self.context.set_ui_value("theme", mode)
        apply_current_theme(self.context)

    def _logout(self) -> None:
        from app.desktop.app import run_login

        user = run_login(self, self.context)
        if user is None:
            return
        self.context.user = user
        self.context.signals.user_changed.emit()
        self.rebuild_for_user()

    def rebuild_for_user(self) -> None:
        for page in list(self.pages.values()):
            self.stack.removeWidget(page)
            page.deleteLater()
        self.pages = {}
        self.current_key = ""
        self.sidebar.rebuild(hidden_keys=self._hidden_nav)
        self.navigate("dashboard")

    def _on_settings_changed(self) -> None:
        # Language / palette / units changed: rebuild every page so texts follow.
        current = self.current_key or "dashboard"
        argument = getattr(self.pages.get(current), "current_argument", None)
        for page in list(self.pages.values()):
            page.deactivate()
            self.stack.removeWidget(page)
            page.deleteLater()
        self.pages = {}
        self.current_key = ""
        self.sidebar.rebuild(hidden_keys=self._hidden_nav)
        self.alert_watcher.refresh_texts()
        self._chrome_loader.request()  # icon set / station symbol may have changed
        if self.tray is not None:
            self._setup_tray_menu()
        self.navigate(current, argument)

    # -- tray / close -------------------------------------------------------------------
    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self._setup_tray_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _setup_tray_menu(self) -> None:
        if self.tray is None:
            return
        menu = QMenu(self)
        show_action = QAction(t("Show window"), menu)
        show_action.triggered.connect(self.show_normal)
        beacon_action = QAction(t("Send beacon"), menu)
        beacon_action.triggered.connect(self._send_beacon)
        quit_action = QAction(t("Exit APRSBox"), menu)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(show_action)
        if self.context.can_edit:
            menu.addAction(beacon_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_normal()

    def show_normal(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def quit_application(self) -> None:
        if not confirm(
            self,
            t("Exit APRSBox"),
            t("Closing APRSBox stops the APRS runtime (digipeater, IGate, beacons and schedulers). Exit now?"),
            danger=True,
            confirm_text=t("Exit"),
        ):
            return
        self._really_quit = True
        self.close()

    def _on_session_end(self, *_args: Any) -> None:
        # Windows log-off / shutdown: close without questions so the runtime stops cleanly.  No close
        # event may follow, so remember the window (maximized or not) right here.
        self._really_quit = True
        self.context.set_ui_value("window_geometry", self.saveGeometry())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        # Only a close requested by the user (title bar X, Alt+F4) may hide the window to the
        # tray or ask for confirmation.  QApplication.quit() and session end send
        # non-spontaneous close events; a modal question there would block the shutdown.
        if not event.spontaneous():
            self._really_quit = True
        if not self._really_quit and self.tray is not None and ctx.minimize_to_tray():
            event.ignore()
            self.hide()
            if not bool(self.context.ui_value("tray_hint_shown", False)):
                self.context.set_ui_value("tray_hint_shown", True)
                self.tray.showMessage(
                    "APRSBox",
                    t("APRSBox keeps running in the notification area. Use the tray icon menu to exit."),
                    QSystemTrayIcon.MessageIcon.Information,
                    6000,
                )
            return
        if not self._really_quit:
            if not confirm(
                self,
                t("Exit APRSBox"),
                t("Closing APRSBox stops the APRS runtime (digipeater, IGate, beacons and schedulers). Exit now?"),
                danger=True,
                confirm_text=t("Exit"),
            ):
                event.ignore()
                return
        self.context.set_ui_value("window_geometry", self.saveGeometry())
        for page in self.pages.values():
            page.deactivate()
        self._chrome_timer.stop()
        self.alert_watcher.stop()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()
