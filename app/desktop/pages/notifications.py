"""Notifications page (web: templates/notifications.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLineEdit

from app.datetime_utils import format_display_datetime
from app.desktop.help import show_help
from app.desktop.i18n import t
from app.desktop.tasks import SerialLoader, run_async
from app.desktop.widgets.common import (
    Card,
    FlashMessage,
    FormGrid,
    Page,
    button,
    checkbox,
    combo,
    combo_value,
    confirm,
    form_flag,
    icon_button,
    key_value_grid,
    label,
    line_edit,
    set_combo_value,
    spin,
    text_edit,
)
from app.desktop.widgets.crud import RowActionBar
from app.desktop.widgets.table import Column, DataTable

TRANSPORT_TYPES = [("webhook", "Webhook"), ("telegram", "Telegram")]


def yes_no(value: Any) -> str:
    return t("Yes") if value else t("No")


class NotificationsPage(Page):
    page_key = "notifications"

    def build(self) -> None:
        self.view: dict[str, Any] = {}
        self.transport_id: int | None = None
        self.rule_id: int | None = None
        self._settings_loaded = False
        manage = self.can_edit

        # Transports -------------------------------------------------------------
        transports = Card(t("Notification transports"))
        transports.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/notifications")))
        self.transport_flash = FlashMessage()
        transports.add(self.transport_flash)
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QHBoxLayout()
        self.transport_table = DataTable(
            [
                Column("name", t("Name"), stretch=True, bold=lambda item: True),
                Column("transport_type_label", t("Type"), width=140, display=lambda item: t(item.get("transport_type_label") or "")),
                Column("enabled", t("Enabled"), width=90, display=lambda item: yes_no(item.get("enabled"))),
                Column("last_test_status", t("Last test"), width=170, display=self._test_status,
                       tooltip=lambda item: str(item.get("last_test_error") or "")),
            ],
            sortable=False,
        )
        self.transport_table.set_empty_text(t("No records yet."))
        self.transport_table.setMinimumHeight(180)
        left_box = Card(variant="inner", margins=(0, 0, 0, 0))
        left_box.add(self.transport_table, 1)
        self.transport_actions = RowActionBar(self.transport_table)
        if manage:
            self.transport_table.row_activated.connect(self._edit_transport)
            self.transport_actions.add(t("Test"), "test-tube-empty", self._test_transport)
            self.transport_actions.add(t("Edit"), "pencil", self._edit_transport)
            self.transport_actions.add(t("Delete"), "delete-outline", self._delete_transport, variant="danger")
        left_box.add(self.transport_actions)
        left.addWidget(left_box)
        row.addLayout(left, 3)
        if manage:
            form_card = Card(variant="inner", margins=(0, 0, 0, 0))
            self.transport_form_title = label(t("New transport"), "h3")
            form_card.add(self.transport_form_title)
            form = FormGrid(2)
            self.t_name = line_edit("")
            form.add_field("name", t("Name"), self.t_name)
            self.t_type = combo(TRANSPORT_TYPES, "webhook")
            self.t_type.currentIndexChanged.connect(self._transport_type_changed)
            form.add_field("transport_type", t("Type"), self.t_type)
            self.t_enabled = checkbox(t("Enabled"))
            form.add_widget(self.t_enabled)
            self.t_url = line_edit("", placeholder="https://example.com/webhook")
            form.add_field("url", t("URL"), self.t_url, span=2)
            self.t_timeout = spin(5, 1, 60, suffix=" s")
            form.add_field("timeout_s", t("Timeout"), self.t_timeout)
            self.t_header = line_edit("")
            form.add_field("secret_header_name", t("Secret header name"), self.t_header)
            self.t_secret = line_edit("")
            self.t_secret.setEchoMode(QLineEdit.EchoMode.Password)
            form.add_field("secret_token", t("Secret token"), self.t_secret)
            self.t_bot = line_edit("")
            form.add_field("bot_token", t("Bot token"), self.t_bot)
            self.t_chat = line_edit("")
            form.add_field("chat_id", t("Chat ID"), self.t_chat)
            self.transport_form = form
            form_card.add(form)
            self.t_keep_secret = label(t("Leave blank to keep the existing secret."), "muted")
            form_card.add(self.t_keep_secret)
            buttons = QHBoxLayout()
            buttons.addWidget(button(t("Save"), icon_name="content-save-outline", on_click=self._save_transport))
            self.t_cancel = button(t("Cancel"), variant="secondary", on_click=self._new_transport)
            buttons.addWidget(self.t_cancel)
            buttons.addStretch(1)
            form_card.add(buttons)
            row.addWidget(form_card, 4)
        else:
            row.addWidget(label(t("Your role can view notification configuration but cannot change it."), "muted", wrap=True), 2)
        transports.add(row)
        self.body.addWidget(transports)

        # Settings ---------------------------------------------------------------
        settings = Card(t("Notification settings"))
        self.settings_flash = FlashMessage()
        settings.add(self.settings_flash)
        if manage:
            self.s_messages = checkbox(t("Enable APRS message notifications"))
            self.s_content = checkbox(t("Include message content"))
            self.s_radar = checkbox(t("Enable radar notifications"))
            for widget in (self.s_messages, self.s_content, self.s_radar):
                settings.add(widget)
            settings.add(label(t("Ignored radar patterns")))
            self.s_ignored = text_edit("", placeholder="SR*, SQ2IBK-3, SQ9MDD*", height=70)
            settings.add(self.s_ignored)
            actions = QHBoxLayout()
            actions.addWidget(button(t("Save"), icon_name="content-save-outline", on_click=self._save_settings))
            actions.addStretch(1)
            settings.add(actions)
        else:
            self.settings_view = Card(variant="inner", margins=(0, 0, 0, 0))
            settings.add(self.settings_view)
        self.body.addWidget(settings)

        # Radar rules --------------------------------------------------------------
        radar = Card(t("Radar rules"))
        self.rule_flash = FlashMessage()
        radar.add(self.rule_flash)
        rules_row = QHBoxLayout()
        rules_row.setSpacing(16)
        rules_box = Card(variant="inner", margins=(0, 0, 0, 0))
        self.rule_table = DataTable(
            [
                Column("enabled", t("Enabled"), width=80, display=lambda item: yes_no(item.get("enabled"))),
                Column("pattern", t("Pattern"), stretch=True, mono=True),
                Column("distance_m", t("Distance (m)"), width=110),
                Column("last_sent_at", t("Last send"), width=170,
                       display=lambda item: format_display_datetime(item.get("last_sent_at"), use_utc=True) if item.get("last_sent_at") else "-"),
                Column("repeat_send_blocked", t("Repeat blocked"), width=120, display=lambda item: yes_no(item.get("repeat_send_blocked"))),
            ],
            sortable=False,
        )
        self.rule_table.set_empty_text(t("No records yet."))
        self.rule_table.setMinimumHeight(160)
        rules_box.add(self.rule_table, 1)
        self.rule_actions = RowActionBar(self.rule_table)
        if manage:
            self.rule_table.row_activated.connect(self._edit_rule)
            self.rule_actions.add(t("Edit"), "pencil", self._edit_rule)
            self.rule_actions.add(t("Delete"), "delete-outline", self._delete_rule, variant="danger")
        rules_box.add(self.rule_actions)
        rules_row.addWidget(rules_box, 3)
        if manage:
            rule_form_card = Card(variant="inner", margins=(0, 0, 0, 0))
            self.rule_form_title = label(t("New radar rule"), "h3")
            rule_form_card.add(self.rule_form_title)
            rule_form = FormGrid(2)
            self.r_pattern = line_edit("", placeholder="SQ6ODL-*")
            rule_form.add_field("pattern", t("Radar rule"), self.r_pattern, span=2)
            self.r_distance = spin(0, 0, 100_000_000, suffix=" m")
            rule_form.add_field("distance_m", t("Distance (m)"), self.r_distance)
            self.r_enabled = checkbox(t("Enabled"), True)
            rule_form.add_widget(self.r_enabled)
            rule_form_card.add(rule_form)
            buttons = QHBoxLayout()
            buttons.addWidget(button(t("Save"), icon_name="content-save-outline", on_click=self._save_rule))
            self.r_cancel = button(t("Cancel"), variant="secondary", on_click=self._new_rule)
            buttons.addWidget(self.r_cancel)
            buttons.addStretch(1)
            rule_form_card.add(buttons)
            rule_form_card.add(label("", None), 1)
            rules_row.addWidget(rule_form_card, 4)
        radar.add(rules_row)
        radar.add(label(t("Radar event log"), "h3"))
        self.log_table = DataTable(
            [
                Column("created_at", t("Time (UTC)"), width=180,
                       display=lambda item: format_display_datetime(item.get("created_at"), use_utc=True)),
                Column("message", t("Message"), stretch=True, mono=True),
            ],
            sortable=False,
        )
        self.log_table.set_empty_text(t("No records yet."))
        self.log_table.setMinimumHeight(180)
        radar.add(self.log_table)
        self.body.addWidget(radar)
        self.body.addStretch(1)

        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000
        if manage:
            self._new_transport()
            self._new_rule()

    # ---------------------------------------------------------------- loading
    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.services.notifications import get_notifications_page_data

        return get_notifications_page_data()

    def _apply(self, view: dict[str, Any]) -> None:
        self.view = view or {}
        self.transport_table.set_rows(self.view.get("notification_transports") or [])
        self.rule_table.set_rows(self.view.get("notification_radar_rules") or [])
        self.log_table.set_rows(self.view.get("notification_radar_event_logs") or [])
        self.transport_actions.sync()
        self.rule_actions.sync()
        settings = self.view.get("notification_settings") or {}
        if self.can_edit:
            if not self._settings_loaded:
                self._settings_loaded = True
                self._load_settings(settings)
        else:
            from app.desktop.widgets.common import clear_layout

            clear_layout(self.settings_view._outer)
            self.settings_view.add(
                key_value_grid(
                    [
                        (t("Enable APRS message notifications"), yes_no(settings.get("messages_enabled"))),
                        (t("Include message content"), yes_no(settings.get("messages_include_content"))),
                        (t("Enable radar notifications"), yes_no(settings.get("radar_enabled"))),
                        (t("Ignored radar patterns"), settings.get("radar_ignored_patterns") or "-"),
                    ]
                )
            )

    def _load_settings(self, settings: dict[str, Any]) -> None:
        self.s_messages.setChecked(bool(settings.get("messages_enabled")))
        self.s_content.setChecked(bool(settings.get("messages_include_content")))
        self.s_radar.setChecked(bool(settings.get("radar_enabled")))
        self.s_ignored.setPlainText(str(settings.get("radar_ignored_patterns") or ""))

    @staticmethod
    def _test_status(item: dict[str, Any]) -> str:
        status = str(item.get("last_test_status") or "")
        if not status:
            return "-"
        when = format_display_datetime(item.get("last_test_at"), use_utc=True) if item.get("last_test_at") else ""
        text = t("OK") if status == "ok" else t("Error")
        return f"{text} · {when}" if when else text

    # ------------------------------------------------------------- transports
    def _transport_type_changed(self) -> None:
        telegram = combo_value(self.t_type) == "telegram"
        for name in ("url", "timeout_s", "secret_header_name", "secret_token"):
            self.transport_form.set_row_visible(name, not telegram)
        for name in ("bot_token", "chat_id"):
            self.transport_form.set_row_visible(name, telegram)

    def _fill_transport(self, form: dict[str, Any]) -> None:
        self.transport_id = form.get("id")
        self.transport_form_title.setText(t("Edit transport") if self.transport_id else t("New transport"))
        self.t_name.setText(str(form.get("name") or ""))
        set_combo_value(self.t_type, form.get("transport_type") or "webhook")
        self.t_enabled.setChecked(bool(form.get("enabled")))
        self.t_url.setText(str(form.get("url") or ""))
        try:
            self.t_timeout.setValue(int(float(form.get("timeout_s") or 5)))
        except (TypeError, ValueError):
            self.t_timeout.setValue(5)
        self.t_header.setText(str(form.get("secret_header_name") or ""))
        self.t_secret.clear()
        self.t_bot.setText(str(form.get("bot_token") or ""))
        self.t_chat.setText(str(form.get("chat_id") or ""))
        self.t_keep_secret.setVisible(bool(self.transport_id))
        self.t_cancel.setVisible(bool(self.transport_id))
        self._transport_type_changed()

    def _new_transport(self) -> None:
        self._fill_transport({"transport_type": "webhook", "enabled": True, "timeout_s": 5})

    def _edit_transport(self, row: dict[str, Any]) -> None:
        if not self.can_edit:
            return
        from app.services.notifications import get_notifications_page_data

        view = get_notifications_page_data(edit_transport_id=int(row["id"]))
        self._fill_transport(view.get("notification_transport_form") or {})

    def _save_transport(self) -> None:
        from app.services.notifications import safe_save_notification_transport

        payload = {
            "name": self.t_name.text(),
            "transport_type": combo_value(self.t_type),
            "enabled": form_flag(self.t_enabled.isChecked()),
            "url": self.t_url.text(),
            "secret_header_name": self.t_header.text(),
            "secret_token": self.t_secret.text(),
            "bot_token": self.t_bot.text(),
            "chat_id": self.t_chat.text(),
            "timeout_s": str(self.t_timeout.value()),
        }
        success, error, _saved_id = safe_save_notification_transport(payload, transport_id=self.transport_id)
        if not success:
            self.transport_flash.error(t(error or "Failed to save notification transport."))
            return
        self.transport_flash.success(t("Notification transport saved."))
        self._new_transport()
        self.refresh()

    def _test_transport(self, row: dict[str, Any]) -> None:
        from app.services.notifications import test_notification_transport

        self.transport_flash.info(t("Working..."))

        def done(result: dict[str, Any]) -> None:
            if result.get("ok"):
                self.transport_flash.success(t("Notification transport test succeeded."))
            else:
                self.transport_flash.error(t(str(result.get("error") or "Notification transport test failed.")))
            self.refresh()

        run_async(lambda: test_notification_transport(int(row["id"])), done,
                  lambda exc: self.transport_flash.error(str(exc)), owner=self)

    def _delete_transport(self, row: dict[str, Any]) -> None:
        if not confirm(self, t("Are you sure?"), t("Delete this entry?") + f"\n\n{row.get('name')}", danger=True, confirm_text=t("Delete")):
            return
        from app.services.notifications import delete_notification_transport

        delete_notification_transport(int(row["id"]))
        if self.transport_id == int(row["id"]):
            self._new_transport()
        self.transport_flash.success(t("Notification transport deleted."))
        self.refresh()

    # --------------------------------------------------------------- settings
    def _save_settings(self) -> None:
        from app.services.notifications import get_notification_settings, safe_save_notification_settings

        success, error = safe_save_notification_settings(
            {
                "messages_enabled": form_flag(self.s_messages.isChecked()),
                "messages_include_content": form_flag(self.s_content.isChecked()),
                "radar_enabled": form_flag(self.s_radar.isChecked()),
                "radar_ignored_patterns": self.s_ignored.toPlainText(),
            }
        )
        if not success:
            self.settings_flash.error(t(error or "Failed to save notification settings."))
            return
        self._load_settings(get_notification_settings())
        self.settings_flash.success(t("Notification settings updated."))

    # ------------------------------------------------------------ radar rules
    def _fill_rule(self, form: dict[str, Any]) -> None:
        self.rule_id = form.get("id")
        self.rule_form_title.setText(t("Edit radar rule") if self.rule_id else t("New radar rule"))
        self.r_pattern.setText(str(form.get("pattern") or ""))
        try:
            self.r_distance.setValue(int(float(form.get("distance_m") or 0)))
        except (TypeError, ValueError):
            self.r_distance.setValue(0)
        self.r_enabled.setChecked(bool(form.get("enabled")))
        self.r_cancel.setVisible(bool(self.rule_id))

    def _new_rule(self) -> None:
        self._fill_rule({"enabled": True, "distance_m": 0})

    def _edit_rule(self, row: dict[str, Any]) -> None:
        if not self.can_edit:
            return
        from app.services.notifications import get_notifications_page_data

        view = get_notifications_page_data(edit_rule_id=int(row["id"]))
        self._fill_rule(view.get("notification_radar_rule_form") or {})

    def _save_rule(self) -> None:
        from app.services.notifications import safe_save_notification_radar_rule

        payload = {
            "enabled": form_flag(self.r_enabled.isChecked()),
            "pattern": self.r_pattern.text(),
            "distance_m": str(self.r_distance.value()),
        }
        success, error, _saved_id = safe_save_notification_radar_rule(payload, rule_id=self.rule_id)
        if not success:
            self.rule_flash.error(t(error or "Failed to save radar rule."))
            return
        self.rule_flash.success(t("Radar rule saved."))
        self._new_rule()
        self.refresh()

    def _delete_rule(self, row: dict[str, Any]) -> None:
        if not confirm(self, t("Are you sure?"), t("Delete this entry?") + f"\n\n{row.get('pattern')}", danger=True, confirm_text=t("Delete")):
            return
        from app.services.notifications import delete_notification_radar_rule

        delete_notification_radar_rule(int(row["id"]))
        if self.rule_id == int(row["id"]):
            self._new_rule()
        self.rule_flash.success(t("Radar rule deleted."))
        self.refresh()
