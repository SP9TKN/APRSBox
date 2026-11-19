"""Shared application state handed to every page."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QSettings, Signal

from app.db import get_app_setting, set_app_setting
from app.models import UserIdentity

REQUIRE_LOGIN_SETTING_KEY = "desktop.require_login"
MINIMIZE_TO_TRAY_SETTING_KEY = "desktop.minimize_to_tray"
START_MINIMIZED_SETTING_KEY = "desktop.start_minimized"
ALERT_SOUND_SETTING_KEY = "desktop.alert_sound_enabled"
STATION_SYMBOL_ICON_SETTING_KEY = "desktop.station_symbol_icon"

LOCAL_USER = UserIdentity(id=0, username="admin", role="admin", is_active=True)


def _flag(key: str, default: bool) -> bool:
    try:
        value = get_app_setting(key)
    except Exception:
        return default
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def require_login() -> bool:
    return _flag(REQUIRE_LOGIN_SETTING_KEY, False)


def set_require_login(enabled: bool) -> None:
    set_app_setting(REQUIRE_LOGIN_SETTING_KEY, "1" if enabled else "0")


def minimize_to_tray() -> bool:
    # Off by default: closing the window ends the program (after one confirmation).
    return _flag(MINIMIZE_TO_TRAY_SETTING_KEY, False)


def set_minimize_to_tray(enabled: bool) -> None:
    set_app_setting(MINIMIZE_TO_TRAY_SETTING_KEY, "1" if enabled else "0")


def start_minimized() -> bool:
    return _flag(START_MINIMIZED_SETTING_KEY, False)


def set_start_minimized(enabled: bool) -> None:
    set_app_setting(START_MINIMIZED_SETTING_KEY, "1" if enabled else "0")


def alert_sound_enabled() -> bool:
    return _flag(ALERT_SOUND_SETTING_KEY, True)


def set_alert_sound_enabled(enabled: bool) -> None:
    set_app_setting(ALERT_SOUND_SETTING_KEY, "1" if enabled else "0")


def station_symbol_icon_enabled() -> bool:
    """Program icon = the station's APRS symbol (like APRSIS32); on by default."""
    return _flag(STATION_SYMBOL_ICON_SETTING_KEY, True)


def set_station_symbol_icon_enabled(enabled: bool) -> None:
    set_app_setting(STATION_SYMBOL_ICON_SETTING_KEY, "1" if enabled else "0")


class AppSignals(QObject):
    settings_changed = Signal()  # language, palette, units, symbol set, ...
    theme_changed = Signal()
    data_changed = Signal(str)  # area name ("modems", "station", "messages", ...)
    runtime_restarted = Signal()
    user_changed = Signal()


@dataclass
class AppContext:
    runtime: Any
    user: UserIdentity = field(default_factory=lambda: LOCAL_USER)
    qsettings: QSettings | None = None
    signals: AppSignals = field(default_factory=AppSignals)
    main_window: Any = None
    navigate: Callable[[str, Any], None] | None = None

    @property
    def can_edit(self) -> bool:
        return self.user.role in {"admin", "operator"}

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    def ui_value(self, key: str, default: Any = None) -> Any:
        if self.qsettings is None:
            return default
        value = self.qsettings.value(key, default)
        return default if value is None else value

    def set_ui_value(self, key: str, value: Any) -> None:
        if self.qsettings is not None:
            self.qsettings.setValue(key, value)

    def go(self, key: str, argument: Any = None) -> None:
        if self.navigate is not None:
            self.navigate(key, argument)

    def notify_data_changed(self, area: str) -> None:
        self.signals.data_changed.emit(area)
