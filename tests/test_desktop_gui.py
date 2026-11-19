"""Off-screen tests of the desktop window (PySide6, QT_QPA_PLATFORM=offscreen).

Every test module run uses its own temporary database seeded with simulated
traffic (tests/desktop_support.py).  The APRS runtime is not started, so
nothing is transmitted and no network or serial port is touched.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("PySide6")

import desktop_support  # noqa: E402

ENV_KEYS = ("APRSBOX_DB_PATH", "APRSBOX_LOG_DIR", "APRSBOX_CONFIG_DIR", "APRSBOX_BACKUPS_DIR")


class Gui:
    def __init__(self, app: Any, window: Any, context: Any, seeded: dict[str, Any]) -> None:
        self.app = app
        self.window = window
        self.context = context
        self.seeded = seeded
        self.errors: list[str] = []

    def pump(self, seconds: float = 0.4) -> None:
        from app.desktop.tasks import wait_for_idle

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            wait_for_idle(50)
            self.app.processEvents()
            time.sleep(0.01)

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.1)
            if predicate():
                return True
        return predicate()

    def open(self, key: str, argument: Any = None, seconds: float = 0.8) -> Any:
        self.window.navigate(key, argument)
        self.pump(seconds)
        page = self.window.current_page()
        assert page is not None, key
        return page


@pytest.fixture(scope="module")
def gui(tmp_path_factory: pytest.TempPathFactory) -> Any:
    saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
    root = tmp_path_factory.mktemp("desktop-gui")
    desktop_support.isolated_environment(root)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    seeded = desktop_support.seed_demo_database(with_alerts=True)
    from app.db import execute
    from app.desktop import context as ctx
    from app.desktop import i18n
    from app.desktop.app import apply_current_theme
    from app.desktop.main_window import MainWindow

    # Offline: tiles from a closed local port, no alarm sounds, no tray dialogs.
    execute("UPDATE map_sources SET url_template = 'http://127.0.0.1:9/{z}/{x}/{y}.png', local_cache_enabled = 0")
    ctx.set_alert_sound_enabled(False)
    i18n.set_language("pl")
    settings = QSettings(str(root / "desktop.ini"), QSettings.Format.IniFormat)
    context = ctx.AppContext(runtime=None, qsettings=settings)
    apply_current_theme(context)
    window = MainWindow(context)
    window.resize(1400, 900)
    window.show()
    harness = Gui(app, window, context, seeded)
    previous_hook = sys.excepthook

    def hook(kind: type[BaseException], value: BaseException, traceback: Any) -> None:
        import traceback as tb

        harness.errors.append("".join(tb.format_exception(kind, value, traceback)))

    sys.excepthook = hook
    harness.pump(1.0)
    try:
        yield harness
    finally:
        sys.excepthook = previous_hook
        window._really_quit = True
        for page in list(window.pages.values()):
            page.deactivate()
        window._chrome_timer.stop()
        window.alert_watcher.stop()
        if window.tray is not None:
            window.tray.hide()
        window.hide()
        harness.pump(0.3)
        window.deleteLater()
        harness.pump(0.2)
        from app.desktop.tiles import shared_tile_manager

        shared_tile_manager().shutdown()
        i18n._language = None  # noqa: SLF001 - reset module state for other tests
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def no_slot_errors(request: pytest.FixtureRequest) -> Any:
    yield
    if "gui" in request.fixturenames:
        harness = request.getfixturevalue("gui")
        errors, harness.errors[:] = list(harness.errors), []
        assert not errors, "\n".join(errors)


def desktop_errors() -> list[str]:
    from app.db import fetch_all

    rows = fetch_all("SELECT message FROM event_logs WHERE category = 'desktop' AND level = 'ERROR' ORDER BY id")
    return [str(row["message"]) for row in rows]


ALL_PAGES = [
    ("dashboard", None), ("map", None), ("stations", None), ("traffic", None), ("alerts", None),
    ("band-condition", None), ("statistics", None), ("modems", None), ("station", None), ("wx", None),
    ("messages", None), ("notifications", None), ("objects", None), ("bulletins", None), ("digi-flows", None),
    ("logs", None), ("users", None), ("settings", None), ("changelog", None),
    ("station-detail", "SP9ABC"), ("station-detail", "SP9MOB-9"), ("alert-detail", 1), ("alert-detail", 2),
    ("alert-send", None), ("frame-detail", 1), ("digi-flow-edit", None), ("digi-flow-edit", 1),
]


def test_every_page_opens_without_errors(gui: Gui) -> None:
    from app.desktop.main_window import _UnavailablePage

    for key, argument in ALL_PAGES:
        page = gui.open(key, argument)
        assert not isinstance(page, _UnavailablePage), key
        assert gui.window.current_key == key
    gui.pump(1.0)
    assert desktop_errors() == []


def test_messages_send_queues_outgoing_message(gui: Gui) -> None:
    from app.db import fetch_one

    page = gui.open("messages", {"callsign": "SP9QWE"})
    assert page._active() is not None and page._active()["callsign"] == "SP9QWE"
    page.composer.setPlainText("Test 73 żółw")  # national characters are stripped
    assert page.composer.toPlainText() == "Test 73 w"
    assert page.composer_error.isVisibleTo(page)
    page.composer.setPlainText("Test 73")
    assert page.send_button.isEnabled()
    page._send()
    gui.pump()
    row = fetch_one("SELECT direction, message_text FROM aprs_messages WHERE message_text = 'Test 73'")
    assert row is not None and row["direction"] == "tx"


def test_messages_open_at_the_newest_message(gui: Gui) -> None:
    from PySide6.QtWidgets import QAbstractSlider

    from app.services.messages import queue_outgoing_message

    page = gui.open("messages", {"callsign": "SP9QWE"})
    for number in range(60):  # a long conversation: its bubbles take a while to lay out
        queue_outgoing_message(callsign="SP9QWE", message_text=f"Scroll {number}", path="")
    page.refresh()
    bar = page.history_scroll.verticalScrollBar()
    assert gui.wait_until(lambda: bar.maximum() > 0 and bar.value() == bar.maximum())
    gui.pump(1.0)
    assert bar.value() == bar.maximum()  # still at the newest after the layout settled

    bar.triggerAction(QAbstractSlider.SliderAction.SliderToMinimum)  # the user scrolls up to read
    gui.pump(0.2)
    assert not page._history_follow
    queue_outgoing_message(callsign="SP9QWE", message_text="Scroll new", path="")
    page.refresh()
    gui.pump(1.0)
    assert bar.value() < bar.maximum()  # reading older messages: a new one does not pull the view down

    gui.open("map", seconds=0.5)
    page = gui.open("messages", seconds=1.0)
    assert page._active()["callsign"] == "SP9QWE"
    assert bar.maximum() > 0 and bar.value() == bar.maximum()  # opened again: the newest message is in view


def test_big_conversation_shows_the_newest_messages_and_adds_new_ones(gui: Gui) -> None:
    from PySide6.QtWidgets import QPushButton

    from app.desktop.i18n import tf
    from app.desktop.pages.messages import HISTORY_WINDOW, MessageBubble
    from app.services.messages import queue_outgoing_message

    page = gui.open("messages", {"callsign": "SP9BIG"})
    for number in range(HISTORY_WINDOW + 15):  # like CQ: hundreds of messages took seconds to build
        queue_outgoing_message(callsign="SP9BIG", message_text=f"Big {number}", path="")
    page.refresh()

    def bubbles() -> list[Any]:  # a rebuild hides the old bubbles and deletes them later
        return [bubble for bubble in page.history_host.findChildren(MessageBubble) if not bubble.isHidden()]

    assert gui.wait_until(lambda: len(bubbles()) == HISTORY_WINDOW)  # the newest first
    older = [button for button in page.history_host.findChildren(QPushButton)
             if button.text() == tf("Older messages ({count})", count=15)]
    assert len(older) == 1
    first = bubbles()[0]
    queue_outgoing_message(callsign="SP9BIG", message_text="Big new", path="")
    page.refresh()
    assert gui.wait_until(lambda: len(bubbles()) == HISTORY_WINDOW + 1)
    assert any(bubble is first for bubble in bubbles())  # added at the end, the others were not rebuilt
    older[0].click()
    assert gui.wait_until(lambda: len(bubbles()) == HISTORY_WINDOW + 16)  # all of them now


def test_message_callsigns_can_be_selected(gui: Gui) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from app.desktop.pages.messages import MessageBubble

    page = gui.open("messages", {"callsign": "SP9QWE"})
    assert page.thread_title.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    message = {"direction": "rx", "sender": "CE4JWI-10", "text": "CQ test", "timestamp": "2026-10-09T08:00:00+00:00"}
    bubble = MessageBubble(message, {"kind": "group", "callsign": "CQ"}, lambda _id: None)
    caption = next(widget for widget in bubble.findChildren(QLabel) if widget.text() == "CE4JWI-10")
    assert caption.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    bubble.deleteLater()


def test_messages_mute_a_sender_from_a_bubble(gui: Gui, monkeypatch: Any) -> None:
    from PySide6.QtCore import Qt

    from app.desktop.pages import messages as messages_module
    from app.desktop.pages.messages import MessageBubble, mute_actions
    from app.services.messages import get_message_settings, queue_outgoing_message, store_incoming_message

    for number, sender in enumerate(("CE4JWI-10", "SP8ABC", "CE4JWI-10")):
        store_incoming_message(sender=sender, addressee="CQ", message_text=f"Mute {number}", message_number=None, path="",
                               timestamp=f"2026-10-09T08:0{number}:00+00:00", acknowledge=False,
                               conversation_callsign="CQ", conversation_kind="group")
    page = gui.open("messages")
    assert gui.wait_until(lambda: any(item.get("callsign") == "CQ" for item in page.conversations))
    page.active_id = str(next(item for item in page.conversations if item.get("callsign") == "CQ")["id"])
    page._render()

    def shown() -> list[str]:
        return [bubble._text for bubble in page.history_host.findChildren(MessageBubble) if not bubble.isHidden()]

    assert gui.wait_until(lambda: shown() == ["Mute 0", "Mute 1", "Mute 2"])
    spam = next(bubble for bubble in page.history_host.findChildren(MessageBubble) if bubble._text == "Mute 0")
    assert [target for _caption, target in spam.mute_actions] == ["CE4JWI-10", "CE4JWI"]
    assert [target for _caption, target in mute_actions("SP8ABC")] == ["SP8ABC"]  # no SSID: all of them
    menus: list[list[str]] = []
    monkeypatch.setattr(messages_module, "popup_menu",
                        lambda menu, _pos: menus.append([action.text() for action in menu.actions() if action.text()]))
    spam._show_menu(spam, spam.rect().center())
    assert menus[-1] == [messages_module.t("Copy"), *[caption for caption, _target in spam.mute_actions]]
    group_item = next(page.list.item(row) for row in range(page.list.count())
                      if page.list.item(row).data(Qt.ItemDataRole.UserRole) == page.active_id)
    page._list_menu(page.list.visualItemRect(group_item).center())
    assert len(menus) == 1  # a group is not a sender: no menu
    queue_outgoing_message(callsign="SP9MUT-3", message_text="Direct", path="")
    page.refresh()
    assert gui.wait_until(lambda: any(item.get("callsign") == "SP9MUT-3" for item in page.conversations))
    direct_id = str(next(item for item in page.conversations if item.get("callsign") == "SP9MUT-3")["id"])
    direct_item = next(page.list.item(row) for row in range(page.list.count())
                       if page.list.item(row).data(Qt.ItemDataRole.UserRole) == direct_id)
    page._list_menu(page.list.visualItemRect(direct_item).center())
    assert menus[-1] == [caption for caption, _target in mute_actions("SP9MUT-3")]
    monkeypatch.setattr(messages_module, "confirm", lambda *args, **kwargs: True)
    spam._on_mute("CE4JWI-10")
    assert gui.wait_until(lambda: shown() == ["Mute 1"])
    assert page.muted_senders.text() == "CE4JWI-10"
    assert get_message_settings()["muted_senders"] == ["CE4JWI-10"]
    page.muted_senders.setText("")  # unmute in the settings field
    page._save_settings()
    assert gui.wait_until(lambda: shown() == ["Mute 0", "Mute 1", "Mute 2"])
    assert get_message_settings()["muted_senders"] == []


def test_messages_enter_sends(gui: Gui) -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from app.db import fetch_one

    page = gui.open("messages", {"callsign": "SP9QWE"})
    page.composer.setPlainText("Enter 73")

    def press(modifiers: Qt.KeyboardModifier) -> None:
        QApplication.sendEvent(page.composer, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, modifiers))

    press(Qt.KeyboardModifier.ShiftModifier)
    assert page.composer.toPlainText() == "Enter 73"  # no second line, nothing sent
    assert fetch_one("SELECT 1 FROM aprs_messages WHERE message_text = 'Enter 73'") is None
    press(Qt.KeyboardModifier.NoModifier)
    gui.pump()
    row = fetch_one("SELECT direction FROM aprs_messages WHERE message_text = 'Enter 73'")
    assert row is not None and row["direction"] == "tx" and page.composer.toPlainText() == ""
    press(Qt.KeyboardModifier.NoModifier)  # empty composer: Enter does nothing
    gui.pump()
    assert fetch_one("SELECT COUNT(*) AS n FROM aprs_messages WHERE message_text = 'Enter 73'")["n"] == 1


def test_message_settings_validation(gui: Gui) -> None:
    from app.desktop.pages.messages import validate_callsign, validate_target_groups

    assert validate_callsign("sp9xyz-15") and not validate_callsign("SP9XYZ-16")
    assert validate_target_groups("cq, yaesu") == (True, ["CQ", "YAESU"], "")
    assert validate_target_groups("CQ,,X")[0] is False
    assert validate_target_groups("BLN1")[0] is False


def test_objects_create_and_delete(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.desktop.pages.objects as objects_module
    from app.db import fetch_one

    page = gui.open("objects")
    panel = page.panels["objects"]
    panel.new()
    panel.name.setText("REPEAT")
    panel.latitude.setText("50.30000")
    panel.longitude.setText("18.70000")
    panel.comment.set_text("Repeater 145.600")
    panel.save()
    gui.pump()
    row = fetch_one("SELECT id, comment FROM aprs_objects WHERE name = 'REPEAT'")
    assert row is not None and row["comment"] == "Repeater 145.600"
    assert panel.edit_id == int(row["id"])
    monkeypatch.setattr(objects_module, "confirm", lambda *args, **kwargs: True)
    panel.delete()
    gui.pump()
    assert fetch_one("SELECT id FROM aprs_objects WHERE name = 'REPEAT'") is None


def test_objects_activation_schedule_round_trip(gui: Gui) -> None:
    from app.db import fetch_one

    page = gui.open("objects")
    panel = page.panels["objects"]
    panel.new()
    panel.name.setText("NET")
    panel.latitude.setText("50.31000")
    panel.longitude.setText("18.71000")
    panel.comment.set_text("Weekly net")
    from app.desktop.widgets.common import set_combo_value

    set_combo_value(panel.activation.mode, "recurring")
    panel.activation.active_from.set_value("2026-01-05 18:00")
    panel.activation.duration_hours.setValue(2)
    panel.activation.interval.setValue(1)
    set_combo_value(panel.activation.unit, "week")
    panel.save()
    gui.pump()
    row = fetch_one("SELECT activation_mode, first_activation_utc, recurrence_duration_minutes, recurrence_interval_unit "
                    "FROM aprs_objects WHERE name = 'NET'")
    assert row is not None
    assert row["activation_mode"] == "recurring"
    assert row["first_activation_utc"] == "2026-01-05 18:00"
    assert int(row["recurrence_duration_minutes"]) == 120
    assert row["recurrence_interval_unit"] == "week"
    # Reloading the record fills the same values back into the editor.
    panel.edit(panel.edit_id)
    assert panel.activation.values()["recurrence_duration_minutes"] == "120"
    assert panel.activation.values()["active_from_utc"] == "2026-01-05 18:00"


def test_bulletins_create(gui: Gui) -> None:
    from app.db import fetch_one
    from app.desktop.widgets.common import set_combo_value

    page = gui.open("bulletins")
    page.new()
    set_combo_value(page.kind, "announcement")
    page.code.setText("a")
    page._code_edited("a")
    assert page.code.text() == "A"
    page.text.set_text("Field day on Saturday")
    page.save()
    gui.pump()
    row = fetch_one("SELECT message_kind, bulletin_code FROM bulletins WHERE message_text = 'Field day on Saturday'")
    assert row is not None and row["message_kind"] == "announcement" and row["bulletin_code"] == "A"


def test_notifications_transport_and_radar_rule(gui: Gui) -> None:
    from app.db import fetch_one
    from app.desktop.widgets.common import set_combo_value

    page = gui.open("notifications")
    page.t_name.setText("Local hook")
    set_combo_value(page.t_type, "webhook")
    page.t_url.setText("http://127.0.0.1:9/hook")
    page._save_transport()
    gui.pump()
    assert fetch_one("SELECT id FROM notification_transports WHERE name = 'Local hook'") is not None
    page.r_pattern.setText("SP9*")
    page.r_distance.setValue(2500)
    page._save_rule()
    gui.pump()
    assert fetch_one("SELECT distance_m FROM notification_radar_rules WHERE pattern = 'SP9*'")["distance_m"] == 2500


def test_users_create_and_last_admin_guard(gui: Gui) -> None:
    from app.db import fetch_one
    from app.desktop.widgets.common import set_combo_value

    page = gui.open("users")
    page.new()
    page.username.setText("op1")
    page.password.setText("secret-123")
    set_combo_value(page.role, "admin")
    page.save()
    gui.pump()
    user = fetch_one("SELECT id, role, is_active FROM users WHERE username = 'op1'")
    assert user is not None and user["role"] == "admin"
    if fetch_one("SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND is_active = 1 AND id != ?", (user["id"],))["total"] == 0:
        page.table.select_key(int(user["id"]))
        page._toggle(dict(user))
        gui.pump()
        assert fetch_one("SELECT is_active FROM users WHERE id = ?", (user["id"],))["is_active"] == 1


def test_users_rename_including_the_signed_in_account(gui: Gui) -> None:
    from app.auth import authenticate_user
    from app.db import fetch_one
    from app.desktop.app import _local_user

    saved_user = gui.context.user
    gui.context.user = _local_user()  # the admin main() signs in when sign-in is off
    page = gui.open("users")
    own_id = gui.context.user.id
    own_name = gui.context.user.username
    try:
        page.edit(own_id)
        assert not page.username.isReadOnly()
        page.username.setText("sp9tkn-admin")
        page.save()
        gui.pump()
        assert fetch_one("SELECT username FROM users WHERE id = ?", (own_id,))["username"] == "sp9tkn-admin"
        assert gui.context.user.username == "sp9tkn-admin"
        assert gui.window.sidebar.username.text() == "sp9tkn-admin"
        # A taken name is refused and nothing changes.
        other = fetch_one("SELECT username FROM users WHERE id != ? ORDER BY id LIMIT 1", (own_id,))
        if other is not None:
            page.edit(own_id)
            page.username.setText(str(other["username"]))
            page.save()
            gui.pump()
            assert fetch_one("SELECT username FROM users WHERE id = ?", (own_id,))["username"] == "sp9tkn-admin"
            assert "już istnieje" in page.form_flash.text() or "already exists" in page.form_flash.text()
    finally:
        page.edit(own_id)
        page.username.setText(own_name)
        page.save()
        gui.pump()
        gui.context.user = saved_user
        gui.window.sidebar.refresh_chrome()
    assert fetch_one("SELECT username FROM users WHERE id = ?", (own_id,))["username"] == own_name
    assert authenticate_user(own_name, "definitely-wrong") is None


def test_digi_flow_editor_creates_rf_flow(gui: Gui) -> None:
    from app.db import fetch_all, fetch_one

    page = gui.open("digi-flow-edit", None)
    page.name.setText("Test digi")
    page.source_combo.setCurrentIndex(page.source_combo.findData("receiver_rf::Test TNC"))
    page.target_combo.setCurrentIndex(page.target_combo.findData("tx_rf::Test TNC"))
    gui.pump(0.2)
    assert [step["step_type"] for step in page.steps] == ["receiver_rf", "tx_rf"]
    page._add_step("filter_dupe")
    page._add_step("filter_path")
    page._add_step("filter_rate_limit")
    # TX RF flows keep the duplicate filter first and the rate + path rules last.
    assert [step["step_type"] for step in page.steps] == ["receiver_rf", "filter_dupe", "filter_rate_limit", "filter_path", "tx_rf"]
    assert not page.palette_items["filter_dupe"].isEnabled()
    path_step = next(step for step in page.steps if step["step_type"] == "filter_path")
    path_step["config"]["trace_paths"] = ["WIDE1-1"]
    page._save()
    gui.pump()
    flow = fetch_one("SELECT id FROM digi_flows WHERE name = 'Test digi'")
    assert flow is not None, page.flash.text()
    steps = [row["step_type"] for row in fetch_all("SELECT step_type FROM digi_flow_steps WHERE flow_id = ? ORDER BY step_order",
                                                   (flow["id"],))]
    assert steps == ["receiver_rf", "filter_dupe", "filter_rate_limit", "filter_path", "tx_rf"]
    assert page.flow_id == int(flow["id"])


def test_digi_flow_editor_aprsis_source_gets_system_rules(gui: Gui) -> None:
    from app.db import execute

    execute("UPDATE modems SET enabled = 1 WHERE name = 'Test TNC'")
    execute(
        "INSERT INTO modems(name, modem_type, band, device_path, baud_rate, enabled, notes, created_at, updated_at) "
        "VALUES ('APRS-IS', 'APRSIS', '', 'r/50/18/100', NULL, 0, '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    try:
        page = gui.open("digi-flow-edit", None)
        page.source_combo.setCurrentIndex(page.source_combo.findData("receiver_aprsis::APRS-IS"))
        gui.pump(0.2)
        index = page.target_combo.findData("tx_rf::Test TNC")
        assert index >= 0
        page.target_combo.setCurrentIndex(index)
        gui.pump(0.2)
        assert [step["step_type"] for step in page.steps] == [
            "receiver_aprsis", "filter_rf_guard", "filter_aprsis_message_delivery", "filter_allow_rules",
            "filter_rf_tx_guard", "tx_rf",
        ]
        assert not page.palette_items["filter_path"].isVisible()
        page.target_combo.setCurrentIndex(page.target_combo.findData("action_log::log-only"))
        gui.pump(0.2)
        assert [step["step_type"] for step in page.steps] == ["receiver_aprsis", "filter_rf_guard", "filter_allow_rules", "action_log"]
    finally:
        execute("DELETE FROM modems WHERE name = 'APRS-IS'")
        execute("UPDATE modems SET enabled = 0 WHERE name = 'Test TNC'")


def test_settings_desktop_map_tile_limit(gui: Gui) -> None:
    from app.desktop import tiles
    from app.desktop.widgets.common import combo_value, set_combo_value

    page = gui.open("settings", seconds=1.2)
    assert gui.wait_until(lambda: bool(page.tile_cache_used.text()))
    assert int(combo_value(page.tile_cache_limit)) == tiles.DEFAULT_TILE_CACHE_LIMIT_MB == 2048
    assert [page.tile_cache_limit.itemText(i) for i in range(page.tile_cache_limit.count())] == \
        ["512 MB", "1 GB", "2 GB", "3 GB", "5 GB", "10 GB"]
    try:
        set_combo_value(page.tile_cache_limit, 1024)
        page._save_desktop()
        assert tiles.tile_cache_limit_mb() == 1024
    finally:
        tiles.set_tile_cache_limit_mb(tiles.DEFAULT_TILE_CACHE_LIMIT_MB)
    # "Fast map" (the user's choice): off by default (a light app), on keeps 512 tiles in RAM.
    manager = tiles.shared_tile_manager()
    assert not page.fast_map.isChecked() and not tiles.fast_map_enabled() and manager.memory_tiles == 96
    try:
        page.fast_map.setChecked(True)
        page._save_desktop()
        assert tiles.fast_map_enabled() and manager.fast and manager.memory_tiles == 512
    finally:
        tiles.set_fast_map(False)
    assert manager.memory_tiles == 96 and not manager.fast


def test_settings_alarm_groups_and_map_sources(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.desktop.pages.settings as settings_module
    from app.db import fetch_one
    from app.services.alarm_groups import get_aprs_alarm_groups

    page = gui.open("settings", seconds=1.2)
    page.alarm_enabled.setChecked(True)
    page.alarm_groups.setText("PL-WARN, NWS-WARN")
    page._save_alarms()
    gui.pump()
    assert get_aprs_alarm_groups() == ["PL-WARN", "NWS-WARN"]
    page._new_map_source()
    page.ms_name.setText("Local tiles")
    page.ms_url.setText("http://127.0.0.1:9/local/{s}/{z}/{x}/{y}.png?key={apiKey}")
    page.ms_subdomains.setText("a,b")
    page.ms_api_key.setText("secret-key")  # e.g. CARTO needs a key of its own
    page._save_map_source()
    gui.pump()
    source = fetch_one("SELECT id, subdomains, api_key FROM map_sources WHERE name = 'Local tiles'")
    assert source is not None and source["subdomains"] == "a,b" and source["api_key"] == "secret-key"
    monkeypatch.setattr(settings_module, "confirm", lambda *args, **kwargs: True)
    page._delete_map_source({"id": source["id"], "name": "Local tiles"})
    gui.pump()
    assert fetch_one("SELECT id FROM map_sources WHERE name = 'Local tiles'") is None


def test_map_bar_switches_the_map_source(gui: Gui) -> None:
    from app.services.map_service import get_default_map_source, safe_delete_map_source, safe_save_map_source

    ok, error, carto_id = safe_save_map_source({
        "name": "CARTO ciemna", "url_template": "http://127.0.0.1:9/dark/{z}/{x}/{y}{r}.png?key={apiKey}",
        "min_zoom": "0", "max_zoom": "20", "subdomains": "a,b,c,d", "api_key": "test", "enabled": "1",
    })
    assert ok, error
    default_id = int(get_default_map_source()["id"])
    page = gui.open("map", seconds=1.0)
    combo = page.tiles_combo
    try:
        assert combo.isVisibleTo(page.top_bar)  # on the sliding top bar, captioned like the time lists
        assert combo.findData(carto_id) >= 0 and combo.currentData() == default_id
        widest = max(combo.fontMetrics().horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
        assert combo.sizeHint().width() > widest  # the whole name shows, not "Wysokc"
        assert any(widget is combo for widget, _caption in page.toolbar_captions)
        combo.setCurrentIndex(combo.findData(carto_id))
        assert page.map.tiles.source.name == "CARTO ciemna" and page.map.native_max_zoom == 20
        assert int(gui.context.ui_value("map/tile_source_id")) == carto_id  # this window remembers it
        assert int(get_default_map_source()["id"]) == default_id  # the default in Settings stays
        assert "CARTO ciemna" in page.tile_label.text()
        page.map.set_view(50.0945, 19.6758, 25)  # super zoom past the source's 20
        assert page.map.zoom == 25
        # The mask list goes past 0 % (CARTO dark was too dark even at 0 %): + lighter, - darker.
        mask = page.mask_combo
        labels = [mask.itemText(i) for i in range(mask.count())]
        assert labels[:7] == ["+50%", "+40%", "+30%", "+20%", "+10%", "0%", "-10%"] and labels[-1] == "-100%"
        osm_mask = mask.currentData()
        mask.setCurrentIndex(mask.findData(-20))
        assert page.map.mask_color.name() == "#ffffff" and abs(page.map.mask_opacity - 0.2) < 1e-9
        combo.setCurrentIndex(combo.findData(default_id))  # each map keeps its own value
        assert mask.currentData() == osm_mask and page.map.mask_color.name() != "#ffffff"
        combo.setCurrentIndex(combo.findData(carto_id))
        assert mask.currentData() == -20 and page.map.mask_color.name() == "#ffffff"
    finally:
        gui.context.set_ui_value("map/tile_source_id", 0)
        page.map.tiles.set_preferred_source(None)
        assert safe_delete_map_source(carto_id)[0]
        page.map.set_view(50.0945, 19.6758, 12)


def test_settings_global_save_rebuilds_pages(gui: Gui) -> None:
    from app.desktop.widgets.common import set_combo_value
    from app.services.content import get_station_settings

    page = gui.open("settings", seconds=1.2)
    set_combo_value(page.units, "imperial")
    page._save_global()
    gui.pump(1.0)
    assert get_station_settings().get("default_units") == "imperial"
    new_page = gui.window.current_page()
    assert new_page is not page and gui.window.current_key == "settings"
    set_combo_value(new_page.units, "metric")
    new_page._save_global()
    gui.pump(1.0)
    assert get_station_settings().get("default_units") == "metric"


def test_alerts_mute_unmute_delete(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.desktop.pages.alerts as alerts_module
    from app.db import fetch_one

    page = gui.open("alerts")
    assert gui.wait_until(lambda: len(page.items) >= 2)
    alert = next(item for item in page.items if item.get("source_callsign") == "SP9EMG-9")
    page._mute(alert)
    gui.pump()
    assert fetch_one("SELECT muted_until FROM aprs_alerts WHERE id = ?", (alert["id"],))["muted_until"]
    page._unmute(alert)
    gui.pump()
    assert not fetch_one("SELECT muted_until FROM aprs_alerts WHERE id = ?", (alert["id"],))["muted_until"]
    monkeypatch.setattr(alerts_module, "confirm", lambda *args, **kwargs: True)
    page._delete(alert)
    gui.pump()
    assert fetch_one("SELECT id FROM aprs_alerts WHERE id = ?", (alert["id"],)) is None


def test_alert_send_page_preselects_area_and_previews(gui: Gui) -> None:
    page = gui.open("alert-send", seconds=1.0)
    assert gui.wait_until(lambda: page.area.count() > 1, timeout=15.0)
    assert page.target.currentData() == "PL-WARN" or page.target.count() >= 1
    assert gui.wait_until(lambda: page.last_preview is not None or page.area.currentData() in ("", None), timeout=15.0)


def test_logs_page_filters_by_level(gui: Gui) -> None:
    from app.desktop.widgets.common import set_combo_value

    page = gui.open("logs")
    set_combo_value(page.level, "WARNING")
    gui.pump()
    assert page.min_level == "WARNING"
    # The rows reload in the background; on a busy machine that takes longer than one pump.
    assert gui.wait_until(lambda: all(row.get("level") in {"WARNING", "ERROR"} for row in page.table.source.rows))


def test_statistics_range_and_window_navigation(gui: Gui) -> None:
    page = gui.open("statistics")
    assert not page.forward_button.isEnabled()
    page._back()
    gui.pump()
    assert page.shift == 1 and page.forward_button.isEnabled()
    page._forward()
    gui.pump()
    assert page.shift == 0


def test_rendered_screen_is_not_blank(gui: Gui, tmp_path: Path) -> None:
    gui.open("dashboard", seconds=1.0)
    image = gui.window.grab().toImage()
    colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 37) for y in range(0, image.height(), 41)}
    assert len(colors) > 8


def test_program_icon_follows_station_symbol(gui: Gui) -> None:
    from PySide6.QtCore import QSize

    from app.desktop import context as ctx
    from app.desktop.icons import app_icon, station_symbol_icon
    from app.services.content import get_aprs_symbol_set, get_station_settings, update_station_settings

    def window_icon() -> object:
        return gui.window.windowIcon().pixmap(QSize(32, 32)).toImage()

    def expected(code: str) -> object:
        return station_symbol_icon("/", code, "", symbol_set=get_aprs_symbol_set()).pixmap(QSize(32, 32)).toImage()

    gui.pump(0.5)
    assert window_icon() == expected("#")  # demo station SQ9TST uses the digipeater symbol
    station = dict(get_station_settings(include_tx_runtime=False))
    try:
        update_station_settings({**station, "symbol_code": "-", "tx_enabled": "1" if station.get("tx_enabled") else ""})
        gui.context.notify_data_changed("station")
        assert gui.wait_until(lambda: window_icon() == expected("-"))
        ctx.set_station_symbol_icon_enabled(False)
        gui.context.notify_data_changed("station")
        assert gui.wait_until(lambda: window_icon() == app_icon().pixmap(QSize(32, 32)).toImage())
    finally:
        ctx.set_station_symbol_icon_enabled(True)
        update_station_settings({**station, "tx_enabled": "1" if station.get("tx_enabled") else ""})
        gui.context.notify_data_changed("station")
        gui.pump(0.5)


def test_interface_form_grows_with_expose_port_details(gui: Gui) -> None:
    page = gui.open("modems")
    for kind, panel in (("TCP", page.tcp_panel), ("SERIALL", page.serial_panel)):
        page.new_interface(kind)
        gui.pump(0.3)
        collapsed = page.stack.height()
        panel.expose.enabled.setChecked(True)
        gui.pump(0.3)
        details = panel.expose.details
        bottom = details.mapTo(page.stack, details.rect().bottomLeft()).y()
        # The whole Expose Port block is inside the (grown) stack, not clipped below it.
        assert details.isVisible() and page.stack.height() > collapsed and bottom <= page.stack.height(), kind
        panel.expose.enabled.setChecked(False)
        gui.pump(0.3)
        assert page.stack.height() == collapsed, kind
    page.new_interface()


def test_traffic_logs_switch_per_interface_and_export(gui: Gui, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QFileDialog

    from app.desktop.pages.traffic import TrafficLogsDialog
    from app.desktop.traffic_logs import enabled_interface_ids
    from app.desktop.widgets.common import set_combo_value

    page = gui.open("traffic")
    dialog = TrafficLogsDialog(page)
    try:
        interface_id, check = next(iter(dialog.log_checks.items()))
        check.setChecked(True)
        assert interface_id in enabled_interface_ids()
        check.setChecked(False)
        assert interface_id not in enabled_interface_ids()
        target = tmp_path / "traffic.txt"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
        set_combo_value(dialog.export_range, "0")
        dialog._export()
        lines = target.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("# APRSBox traffic export") and len(lines) > 1
        assert str(target) in dialog.flash.text()
    finally:
        dialog.deleteLater()
    # The switch next to each interface row in the monitor.
    page._toggle_log(interface_id, "Test TNC", True)
    assert interface_id in enabled_interface_ids() and interface_id in page.snapshot["log_enabled"]
    page._toggle_log(interface_id, "Test TNC", False)
    assert interface_id not in enabled_interface_ids()


def test_map_track_dots_carry_their_frames(gui: Gui) -> None:
    page = gui.open("map", seconds=1.0)
    points = [point for track in page.payload.get("mobile_tracks") or [] for point in track.get("points") or []]
    assert all("frame_id" in point and "line" in point and "source_kind" in point for point in points)
    for shape in page.map.layers.get("tracks") or []:
        assert shape.point_tooltips and len(shape.point_tooltips) == len(shape.points) == len(shape.point_payloads)


def test_right_click_on_a_track_dot_draws_the_way_to_the_receiver(gui: Gui) -> None:
    from PySide6.QtCore import QPoint

    own = (desktop_support.OWN_LATITUDE, desktop_support.OWN_LONGITUDE)
    for key, argument in (("map", None), ("station-detail", "SP9MOB-9")):
        page = gui.open(key, argument, seconds=1.0)
        assert gui.wait_until(lambda: any(shape.point_payloads[0].get("display_callsign") == "SP9MOB-9"
                                          for shape in page.map.layers.get("tracks") or []))
        mobile = next(shape for shape in page.map.layers["tracks"]
                      if shape.point_payloads[0].get("display_callsign") == "SP9MOB-9")
        # The mobile's frames were heard directly over RF: one line from the dot to the own station.
        page.map.track_points_selected.emit([mobile.point_payloads[0]], QPoint())
        try:
            assert [line.points for line in page.map.layers["reception"]] == [[mobile.points[0], own]], key
            page.refresh()
            gui.pump(1.0)
            assert len(page.map.layers["reception"]) == 1, key  # kept when the page refreshes
        finally:
            page.map.track_points_selected.emit([], QPoint())
        assert page.map.layers["reception"] == [], key

    # Stations too: SP9QWE was heard through the digipeater SR9XSR.
    page = gui.open("map", seconds=1.0)
    assert gui.wait_until(lambda: any(marker.key == "SP9QWE" for marker in page.map.markers))
    marker = next(marker for marker in page.map.markers if marker.key == "SP9QWE")
    digi = desktop_support.DIGIS["SR9XSR"][:2]
    page.map.marker_right_clicked.emit(marker.payload, QPoint())
    try:
        assert [line.points for line in page.map.layers["reception"]] == [
            [(marker.latitude, marker.longitude), digi], [digi, own]]
    finally:
        page.map.track_points_selected.emit([], QPoint())


def test_traffic_retention_offers_up_to_96_hours(gui: Gui) -> None:
    from app.db import get_traffic_retention_minutes, normalize_traffic_retention_minutes

    page = gui.open("settings")
    values = [page.retention.itemData(index) for index in range(page.retention.count())]
    labels = [page.retention.itemText(index) for index in range(page.retention.count())]
    assert values[-4:] == [1440, 2880, 4320, 5760] and labels[-3:] == ["48h", "72h", "96h"]
    assert normalize_traffic_retention_minutes("5760") == 5760 and normalize_traffic_retention_minutes("9999") == 60
    assert get_traffic_retention_minutes() in values


def test_map_toolbar_stays_one_row_in_a_narrow_window(gui: Gui) -> None:
    from PySide6.QtCore import Qt

    page = gui.open("map", seconds=1.0)

    def row_height() -> int:
        page._set_top_bar_shown(True, animate=False)
        gui.pump(0.4)
        return page.top_bar.height()

    def chips() -> list[Any]:  # the page rebuilds the chips on every station refresh
        return [page.filters_layout.itemAt(index).widget() for index in range(page.filters_layout.count())]

    try:
        wide = row_height()
        assert chips() and all(chip.text() == chip.property("full_text") for chip in chips())
        gui.window.resize(900, 900)  # as narrow as the window gets
        assert row_height() == wide  # no second row: the same height
        assert page.filters_host.height() <= page.reset_button.height() + 4  # the chips share the icons' row
        page._fit_toolbar_row(100_000)  # full names, to measure them (this narrow window shows less)
        full_width = sum(widget.sizeHint().width() + 6 for widget in
                         (page.toolbar_row.itemAt(index).widget() for index in range(page.toolbar_row.count()))
                         if widget is not None and widget is not page.filters_host and widget.isVisibleTo(page.top_bar))
        full_width += sum(chip.sizeHint().width() + 6 for chip in chips())
        page._fit_toolbar_row(full_width)  # just too narrow for the names (the margins do not fit)
        assert all(chip.text() == chip.property("short_text") for chip in chips())  # only the band
        page._fit_toolbar_row(300)  # far too narrow even for that
        assert all(chip.text() == "" and chip.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
                   for chip in chips())
    finally:
        gui.window.resize(1400, 900)
        gui.pump(0.4)
        page._set_top_bar_shown(False, animate=False)
    page._fit_toolbar_row(page.body_widget.width())
    assert all(chip.text() == chip.property("full_text") for chip in chips())


def test_map_time_lists_have_captions_underneath(gui: Gui) -> None:
    from PySide6.QtCore import QPoint

    from app.desktop.i18n import t

    page = gui.open("map", seconds=1.0)
    page._set_top_bar_shown(True, animate=False)
    gui.pump(0.4)
    tiles, zoom, legend = page.tile_label.text(), page.zoom_label.text(), page.paths_legend.text()
    map_caption, history, objects, paths = (caption for _combo, caption in page.toolbar_captions)

    def info_line(tiles_text: str, zoom_text: str, legend_text: str) -> None:
        # Checked straight away: the page rewrites these texts itself (tile status, legend counts).
        page.tile_label.setText(tiles_text)
        page.zoom_label.setText(zoom_text)
        page.paths_legend.setText(legend_text)
        page.top_bar.layout().activate()

    def shown(caption: Any) -> bool:  # Qt's two flags agree (a hide nested in a show left them apart)
        assert caption.isVisible() != caption.isHidden()
        return caption.isVisible()

    assert not page.paths_visible
    try:
        info_line("Tiles: OpenStreetMap", "Zoom: 12", "")  # the usual texts, no provider status
        assert shown(history) and shown(objects) and not shown(paths)  # the RF paths list is hidden with the paths
        assert map_caption.text() == t("Map") and not shown(map_caption)  # one map source: no list to pick from
        page._toggle_paths()
        info_line("Tiles: OpenStreetMap", "Zoom: 12", "")
        for (combo, caption), text in zip(page.toolbar_captions[1:], ("Traffic history", "Object history", "RF paths"), strict=True):
            assert caption.text() == t(text) and shown(caption)
            origin = combo.mapTo(page.top_bar, QPoint(0, 0))
            assert caption.y() >= origin.y() + combo.height()  # under its list ...
            assert abs(caption.geometry().center().x() - (origin.x() + combo.width() // 2)) <= 1  # ... centred
            assert caption.y() == page.zoom_label.y()  # on the info line: the bar is no taller
        info_line("Tiles: OpenStreetMap", "Zoom: 12", "x" * 200)  # the legend reaching a list hides its caption
        assert not shown(paths)
        info_line("Tiles: OpenStreetMap", "x" * 200, "")  # so does an info text on the left: no overlap
        assert not shown(history) and not shown(objects) and not shown(paths)
        info_line("Tiles: OpenStreetMap", "Zoom: 12", "")  # and they come back
        assert shown(history) and shown(objects) and shown(paths)
    finally:
        if page.paths_visible:
            page._toggle_paths()
        info_line(tiles, zoom, legend)
        page._set_top_bar_shown(False, animate=False)


def test_map_refreshes_read_through_one_connection(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import app.db as db
    from app.desktop import tasks
    from app.services import content

    page = gui.open("map", seconds=1.0)
    tasks.wait_for_idle()
    opened: list[int] = []
    original = db.connect

    def counting() -> Any:
        opened.append(threading.get_ident())
        return original()

    monkeypatch.setattr(db, "connect", counting)

    def connections(load: Any) -> tuple[Any, int]:
        opened.clear()
        result = load()
        return result, opened.count(threading.get_ident())

    content._TRAFFIC_SNAPSHOT_CACHE.clear()  # a fresh snapshot, not the one held for a second
    snapshot, count = connections(page._load_traffic)  # every 2 s
    assert snapshot.get("frames") and count == 1
    paths_visible, station_settings = page.paths_visible, page.station_settings
    page.paths_visible, page.station_settings = True, {}
    try:
        _routes, count = connections(page._load_routes)  # every second for the short windows
        assert count == 1
    finally:
        page.paths_visible, page.station_settings = paths_visible, station_settings


def test_map_refreshes_slower_while_nobody_is_at_the_computer(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop import idle
    from app.desktop.pages import map_page as module

    page = gui.open("map", seconds=1.0)
    assert not page._idle and page._idle_timer.isActive()
    assert page._traffic_timer.interval() == module.TRAFFIC_REFRESH_MS
    monkeypatch.setenv(idle.SLOWDOWN_ENV, "1")
    away = idle.IDLE_AFTER_SECONDS + 1.0
    monkeypatch.setattr(idle, "seconds_since_input", lambda: away)
    requested: list[str] = []
    try:
        page._check_idle()
        assert page._idle
        assert page._traffic_timer.interval() == module.IDLE_TRAFFIC_REFRESH_MS
        assert page._station_timer.interval() == module.IDLE_STATION_REFRESH_MS
        assert page._routes_interval_ms() >= module.IDLE_ROUTES_REFRESH_MS
        assert page._traffic_timer.isActive() and page._station_timer.isActive()  # slower, not stopped
        monkeypatch.setattr(page._traffic_loader, "request", lambda: requested.append("traffic"))
        monkeypatch.setattr(page._station_loader, "request", lambda: requested.append("stations"))
        away = 2.0  # somebody moved the mouse
        page._check_idle()
        assert not page._idle and {"traffic", "stations"} <= set(requested)  # the current state at once
        assert page._traffic_timer.interval() == module.TRAFFIC_REFRESH_MS
        assert page._station_timer.interval() == module.STATION_REFRESH_MS
    finally:
        away = 0.0
        page._check_idle()
    assert not page._idle


def test_station_detail_shows_weather_charts_for_a_weather_station(gui: Gui) -> None:
    page = gui.open("station-detail", "SP9XWX-13", seconds=1.5)
    panel = page.weather_panel
    assert page.weather_card.isVisible()
    titles = {chart.spec.title for chart in panel.visible_charts()}
    assert {"Temperature", "Humidity", "Pressure", "Wind", "Wind direction"} <= titles  # what SP9XWX-13 reports
    assert "Sunlight" not in titles  # nor what it does not
    assert panel.range_buttons[24].isChecked()
    panel.set_range(168)
    assert panel.range_buttons[168].isChecked() and not panel.range_buttons[24].isChecked()
    chart = panel.visible_charts()[0]
    assert chart.has_values()
    moment = chart.points[-1][0]
    chart.hovered.emit(moment)  # one crosshair on every chart
    assert all(item.hover_time == moment for item in panel.charts)
    chart.hovered.emit(None)
    other = gui.open("station-detail", "SP9ABC", seconds=1.5)
    assert not other.weather_card.isVisible()  # no weather reports: no card


def test_station_detail_lists_the_frames_of_the_whole_retention(gui: Gui) -> None:
    from app.db import get_traffic_retention_minutes
    from app.desktop.i18n import tf
    from app.desktop.pages.traffic import format_minutes

    page = gui.open("station-detail", "SP9MOB-9", seconds=1.5)
    rows = page.packets_table.model().rowCount()
    assert rows > 0 and page.packets_note.isVisible()
    assert page.packets_note.text() == tf("Frames from the last {retention} (traffic history retention): {count}",
                                          retention=format_minutes(get_traffic_retention_minutes()), count=rows)


def test_settings_switch_cwop_weather_stations(gui: Gui) -> None:
    from app.services.cwop import get_cwop_settings, save_cwop_settings

    page = gui.open("settings", seconds=1.0)
    assert not page.cwop_enabled.isChecked()  # off unless switched on
    assert page.cwop_status.text().startswith("Stan:")
    assert page.cwop_range.text().startswith("Zasięg: ")  # the radius of the APRS-IS filter, not a setting
    try:
        page.cwop_enabled.setChecked(True)
        page._save_cwop()
        assert get_cwop_settings()["enabled"]
    finally:
        save_cwop_settings(enabled=False)
        page.cwop_enabled.setChecked(False)


def test_map_object_history_limits_the_stations(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timedelta, timezone

    from app.desktop.pages import map_page as module
    from app.desktop.rf_paths import own_station_identity

    page = gui.open("map", seconds=1.0)
    combo = page.objects_window_combo
    assert page.objects_window == module.DEFAULT_OBJECT_WINDOW_SECONDS == 3600 == combo.currentData()
    now = datetime.now(timezone.utc)

    def station(call: str, minutes_ago: float) -> dict[str, Any]:
        stamp = (now - timedelta(minutes=minutes_ago)).isoformat()
        return {"display_callsign": call, "callsign": call, "latitude": 49.9, "longitude": 19.9,
                "last_heard_at": stamp, "last_seen_any_at": stamp}

    own = own_station_identity(page.station_settings)
    assert own
    monkeypatch.setattr(page, "payload", {
        **page.payload, "mobile_tracks": [],
        "stations": [station("SP9NEW", 10), station("SP9OLD", 61), station(own, 300)],
    })

    def shown() -> set[str]:
        return {item["display_callsign"] for item in page._filtered_data()[0]}

    assert shown() == {"SP9NEW", own}  # an hour without a frame hides it; my station always stays
    try:
        combo.setCurrentIndex(combo.findData(7200))
        assert shown() == {"SP9NEW", "SP9OLD", own}
        assert int(gui.context.ui_value("map/object_window_seconds")) == 7200
        combo.setCurrentIndex(combo.findData(300))
        assert shown() == {own}
    finally:
        combo.setCurrentIndex(combo.findData(module.DEFAULT_OBJECT_WINDOW_SECONDS))


def test_map_traffic_history_window_limits_the_tracks(gui: Gui) -> None:
    from app.desktop.pages.map_page import DEFAULT_TRACK_WINDOW_SECONDS

    page = gui.open("map", seconds=1.0)
    combo = page.history_window_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [
        page.paths_window_combo.itemData(i) for i in range(page.paths_window_combo.count())]
    assert page.history_window == DEFAULT_TRACK_WINDOW_SECONDS and combo.currentData() == DEFAULT_TRACK_WINDOW_SECONDS
    assert page.map.layers["tracks"]  # SP9MOB-9 drove within the last minutes
    stations_before = len(page.map.markers)
    try:
        combo.setCurrentIndex(combo.findData(1))  # 1 s: no track is that fresh
        assert page.map.layers["tracks"] == [] and len(page.map.markers) == stations_before  # markers stay
        assert int(gui.context.ui_value("map/track_window_seconds")) == 1
    finally:
        combo.setCurrentIndex(combo.findData(DEFAULT_TRACK_WINDOW_SECONDS))
    assert page.map.layers["tracks"]


def test_map_repaints_rf_paths_only_when_they_change(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.rf_paths import route_from_frame

    page = gui.open("map", seconds=1.0)
    painted: list[int] = []
    monkeypatch.setattr(page, "_render_paths", lambda *args, **kwargs: painted.append(1))
    route = route_from_frame("SP9QWE>APRS,SR9XSR*,WIDE2-1:>x", source_kind="rf", own_callsign="SQ9TST",
                             heard_at="2026-10-09T10:00:00+00:00")
    page._routes_signature = None
    page._apply_routes([route])
    page._apply_routes([route])  # the 1 s reload brought nothing new: no repaint
    assert len(painted) == 1
    page._apply_routes([])
    assert len(painted) == 2 and page.routes == []


def test_rf_path_windows_from_one_second_to_six_hours(gui: Gui) -> None:
    from app.desktop.pages.map_page import stored_paths_window

    page = gui.open("map", seconds=0.8)
    combo = page.paths_window_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [1, 5, 10, 15, 30, 60, 300, 900, 1800, 3600, 7200, 21600]
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "1 s", "5 s", "10 s", "15 s", "30 s", "1 min", "5 min", "15 min", "30 min", "1 h", "2 h", "6 h"]
    try:
        combo.setCurrentIndex(combo.findData(15))
        assert page.paths_window == 15 and page._routes_interval_ms() == 1000
        assert int(gui.context.ui_value("map/rf_paths_window_seconds")) == 15
        combo.setCurrentIndex(combo.findData(21600))
        assert page._routes_interval_ms() == 10000
    finally:
        combo.setCurrentIndex(combo.findData(3600))

    class Saved:
        def __init__(self, values: dict[str, Any]) -> None:
            self.values = values

        def ui_value(self, key: str, default: Any = None) -> Any:
            return self.values.get(key, default)

    # A window saved in minutes by the earlier version snaps to the nearest new option.
    assert stored_paths_window(Saved({"map/rf_paths_window_minutes": 1440})) == 21600
    assert stored_paths_window(Saved({"map/rf_paths_window_minutes": 60})) == 3600
    assert stored_paths_window(Saved({"map/rf_paths_window_seconds": 30})) == 30
    assert stored_paths_window(Saved({})) == 3600


def test_map_toolbar_slides_out_when_the_mouse_comes_to_the_top_edge(gui: Gui) -> None:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    page = gui.open("map", seconds=1.0)
    body = page.body_widget
    assert page.reset_button.parent() is page.top_bar and page.tile_label.parent() is page.top_bar
    assert page.top_bar.parent() is body  # part of the window, not a panel over the map
    assert not page.top_bar_shown and page.top_bar.geometry().bottom() < 0  # hidden: the map has the whole height
    assert page.map.y() < 20  # nothing above the map but the page margin

    def move(widget: Any, y: float) -> None:
        point = QPointF(300, y)
        QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, point, widget.mapToGlobal(point),
                                                   Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier))

    try:
        move(page.map, 200)
        assert not page.top_bar_shown  # the middle of the map does not open it
        move(page.map, 2)
        assert page.top_bar_shown
        gui.pump(0.5)
        assert page.top_bar.geometry().topLeft() == QPoint(0, 0) and page.top_bar.width() == body.width()
        assert page.top_bar.isVisible() and page.reset_button.isVisible()
        page._hide_top_bar_when_idle(body.mapToGlobal(QPoint(300, page.top_bar.height() - 5)))
        assert page.top_bar_shown  # the mouse is on the bar: it stays
        page._hide_top_bar_when_idle(body.mapToGlobal(QPoint(300, body.height() - 20)))
        assert not page.top_bar_shown
        gui.pump(0.5)
        assert page.top_bar.geometry().bottom() < 0
        move(body, 3)  # the page margin above the map is the top edge as well
        assert page.top_bar_shown
    finally:
        page._set_top_bar_shown(False, animate=False)


def test_session_end_remembers_the_window(gui: Gui) -> None:
    # Windows log-off (and a restart through it) sends no close event, so the window state is saved right away.
    gui.context.set_ui_value("window_geometry", b"")
    try:
        gui.window._on_session_end()
        saved = gui.context.ui_value("window_geometry")
        assert saved and bytes(saved) == bytes(gui.window.saveGeometry())
    finally:
        gui.window._really_quit = False


def test_map_station_list_grows_upwards_with_the_newest_at_the_bottom(gui: Gui) -> None:
    page = gui.open("map", seconds=1.0)

    def entry(number: int) -> dict[str, Any]:
        call = f"SP9N{number:03d}"
        return {"timestamp": f"t{number:03d}", "label": call, "digi": "-", "frame": {}, "lookup": None, "station": call}

    entries = [entry(number) for number in range(60, 0, -1)]  # newest first, as _scroller_entries builds them
    try:
        page._scroller_signature = None
        page._render_scroller(entries)
        texts = [page.scroller_list.item(index).text() for index in range(page.scroller_list.count())]
        assert texts[0].startswith("SP9N001") and texts[-1].startswith("SP9N060")
        bar = page.scroller_list.verticalScrollBar()
        assert bar.maximum() > 0 and bar.value() == bar.maximum()  # follows the newest frame
        # Scrolled up to read older frames: a new frame does not pull the view down.
        bar.setValue(0)
        page._render_scroller([entry(61), *entries])
        assert page.scroller_list.item(page.scroller_list.count() - 1).text().startswith("SP9N061") and bar.value() == 0
    finally:
        page._scroller_signature = None
        page._render_scroller_and_latest()


def test_map_reset_view_puts_own_station_in_the_middle_of_the_uncovered_map(gui: Gui) -> None:
    from app.desktop.pages.map_page import STATION_VIEW_ZOOM

    page = gui.open("map", seconds=1.0)

    def own_on_screen() -> tuple[float, float]:
        origin = page.map._origin()
        x, y = page.map._world_point(desktop_support.OWN_LATITUDE, desktop_support.OWN_LONGITUDE)
        return x - origin[0], y - origin[1]

    page.map.set_view(10.0, 10.0, 5)
    page._reset_view()
    assert page.map.zoom == STATION_VIEW_ZOOM
    anchor = page._visible_map_center()
    assert anchor is not None and anchor.x() < page.map.width() / 2  # the station list covers the right side
    x, y = own_on_screen()
    assert abs(x - anchor.x()) < 1 and abs(y - anchor.y()) < 1
    # With the panels on the right hidden the station is in the middle of the map again.
    shown = (page.latest_visible, page.scroller_visible)
    try:
        page.latest_visible = page.scroller_visible = False
        page._reset_view()
        x, y = own_on_screen()
        assert abs(x - page.map.width() / 2) < 1 and abs(y - page.map.height() / 2) < 1
    finally:
        page.latest_visible, page.scroller_visible = shown


def test_map_shows_own_station_from_settings(gui: Gui) -> None:
    page = gui.open("map", seconds=1.0)

    def own_markers() -> list[Any]:
        return [marker for marker in page.map.markers if marker.own and marker.key == desktop_support.OWN_CALLSIGN]

    # The demo traffic has no frame of the own station, so it stands at its My Station position.
    assert gui.wait_until(lambda: len(own_markers()) == 1)
    marker = own_markers()[0]
    assert (marker.latitude, marker.longitude) == (desktop_support.OWN_LATITUDE, desktop_support.OWN_LONGITUDE)
    page._open_station(marker.payload)
    gui.pump(0.3)
    assert gui.window.current_key == "station"


CLICK_PAGES = [
    ("dashboard", None), ("map", None), ("stations", None), ("traffic", None), ("alerts", None), ("band-condition", None),
    ("statistics", None), ("modems", None), ("station", None), ("wx", None), ("messages", None), ("notifications", None),
    ("objects", None), ("bulletins", None), ("digi-flows", None), ("logs", None), ("users", None), ("changelog", None),
    ("station-detail", "SP9ABC"), ("alert-detail", 2), ("alert-send", None), ("frame-detail", 1),
    ("digi-flow-edit", 1), ("digi-flow-edit", None), ("settings", None),
]
HOST_POWER_TEXTS = ("Reboot host", "Power off host", "Restart hosta", "Wyłącz host")


def test_clicking_every_button_raises_nothing(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    """Click every enabled button on every page (row actions included) with dialogs stubbed."""
    import importlib
    import pkgutil
    import socket
    import subprocess

    import shiboken6
    from PySide6.QtWidgets import QAbstractButton, QDialog, QFileDialog, QListWidget, QMessageBox, QTableView

    import app.desktop.main_window as main_window_module
    import app.desktop.pages as pages_package
    from app.desktop.widgets.table import DataTable

    real_getaddrinfo = socket.getaddrinfo

    def localhost_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in ("127.0.0.1", "localhost", "::1", None, ""):
            raise socket.gaierror(f"offline test: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    def no_subprocess(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"subprocess must not run in tests: {args}")

    monkeypatch.setattr(socket, "getaddrinfo", localhost_only)
    monkeypatch.setattr(subprocess, "run", no_subprocess)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    answer = {"value": False}

    def fake_confirm(*args: Any, **kwargs: Any) -> bool:
        return answer["value"]

    monkeypatch.setattr(main_window_module, "confirm", fake_confirm)
    for module_info in pkgutil.iter_modules(pages_package.__path__):
        module = importlib.import_module(f"app.desktop.pages.{module_info.name}")
        if hasattr(module, "confirm"):
            monkeypatch.setattr(module, "confirm", fake_confirm)

    def reselect(page: Any) -> None:
        for table in [widget for widget in page.findChildren(QTableView) if isinstance(widget, DataTable)]:
            if table.isVisibleTo(page) and table.model().rowCount() > 0 and table.current_row() is None:
                table.selectRow(0)
        for listing in page.findChildren(QListWidget):
            if listing.isVisibleTo(page) and listing.count() > 0 and listing.currentRow() < 0:
                listing.setCurrentRow(0)
        gui.pump(0.05)

    total = 0
    for answer["value"] in (False, True):
        for key, argument in CLICK_PAGES:
            page = gui.open(key, argument, seconds=0.5)
            done: set[tuple[Any, ...]] = set()
            for _round in range(12):
                reselect(page)
                buttons = [button for button in page.findChildren(QAbstractButton) if button.isVisibleTo(page) and button.isEnabled()]
                navigated = False
                for index, button in enumerate(buttons):
                    if not shiboken6.isValid(button) or not shiboken6.isValid(page):
                        navigated = True
                        break
                    ident = (button.text(), button.toolTip(), index)
                    if ident in done:
                        continue
                    done.add(ident)
                    text = button.text() or button.toolTip()
                    if any(item in text for item in HOST_POWER_TEXTS):
                        continue
                    if answer["value"] and any(item in text for item in ("Usuń", "Delete", "Wyczyść", "Reset")):
                        continue  # keep the demo data for the other tests
                    button.click()
                    total += 1
                    gui.pump(0.03)
                    if gui.window.current_key != key or not shiboken6.isValid(page):
                        page = gui.open(key, argument, seconds=0.3)
                        navigated = True
                        break
                if not navigated:
                    break
    gui.pump(0.5)
    assert total > 300
    assert desktop_errors() == []
