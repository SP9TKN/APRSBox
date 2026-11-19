"""Users / Roles page (web: templates/users.html, routers/admin.py)."""

from __future__ import annotations

import secrets
import sqlite3
import string
from dataclasses import replace
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLineEdit

from app.datetime_utils import format_display_datetime
from app.desktop.i18n import t, tf
from app.desktop.tasks import SerialLoader
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
    label,
    line_edit,
    set_combo_value,
    tone_color,
)
from app.desktop.widgets.crud import RowActionBar
from app.desktop.widgets.table import Column, DataTable

PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 16


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def other_active_admins(user_id: int | None) -> int:
    """Active admin accounts other than ``user_id`` (sign-in must stay possible)."""
    from app.db import fetch_one

    row = fetch_one(
        "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND is_active = 1 AND id != ?",
        (int(user_id or 0),),
    )
    return int(row["total"] or 0) if row is not None else 0


LAST_ADMIN_ERROR = "At least one active admin account is required."


class UsersPage(Page):
    page_key = "users"

    def build(self) -> None:
        from app.models import ROLES

        self.edit_id: int | None = None
        self.page_flash = FlashMessage()
        self.body.addWidget(self.page_flash)
        row = QHBoxLayout()
        row.setSpacing(14)

        list_card = Card(t("Users List"))
        self.table = DataTable(
            [
                Column("username", t("Username"), stretch=True, bold=lambda item: True),
                Column("role", t("Role"), width=110),
                Column("is_active", t("Status"), width=110,
                       display=lambda item: t("Active") if item.get("is_active") else t("Disabled"),
                       foreground=lambda item: tone_color("ok" if item.get("is_active") else "error")),
                Column("last_login_at", t("Last login"), width=170,
                       display=lambda item: format_display_datetime(item["last_login_at"]) if item.get("last_login_at") else t("Never")),
            ],
            sortable=False,
        )
        self.table.set_empty_text(t("No records yet."))
        self.table.setMinimumHeight(320)
        self.table.row_activated.connect(lambda item: self.edit(int(item["id"])))
        self.table.clicked.connect(lambda _index: self._clicked())
        list_card.add(self.table, 1)
        self.actions = RowActionBar(self.table)
        self.toggle_button = self.actions.add(t("Disable user"), "account-off", self._toggle,
                                              enabled_for=lambda item: not self._is_current(item))
        self.actions.add(t("Delete user"), "delete-outline", self._delete, variant="danger",
                         enabled_for=lambda item: not self._is_current(item))
        self.table.selection_changed_rows.connect(lambda _rows: self._sync_toggle_label())
        list_card.add(self.actions)
        row.addWidget(list_card, 3)

        self.form_card = Card(t("Create User"))
        form = FormGrid(1)
        self.username = line_edit("")
        form.add_field("username", t("Username"), self.username)
        password_row = QHBoxLayout()
        self.password = line_edit("")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        password_row.addWidget(self.password, 1)
        password_row.addWidget(button(t("Generate"), variant="secondary", on_click=self._generate))
        self.visibility_button = button(t("Show"), variant="secondary", on_click=self._toggle_visibility)
        password_row.addWidget(self.visibility_button)
        from app.desktop.widgets.common import wrap_layout

        form.add_field("password", t("Password"), wrap_layout(password_row))
        self.role = combo([(role, role) for role in ROLES], "viewer")
        form.add_field("role", t("Role"), self.role)
        self.active = checkbox(t("User is active"), True)
        form.add_widget(self.active)
        self.form = form
        self.form_card.add(form)
        self.form_flash = FlashMessage()
        self.form_card.add(self.form_flash)
        buttons = QHBoxLayout()
        self.save_button = button(t("Create User"), icon_name="content-save-outline", on_click=self.save)
        buttons.addWidget(self.save_button)
        self.cancel_button = button(t("Cancel"), variant="secondary", on_click=self.new)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        self.form_card.add(buttons)
        self.form_card.add(label(t("Users and roles are used when sign-in is required (Settings → Desktop application)."), "small", wrap=True))
        self.form_card.add(label(""), 1)
        row.addWidget(self.form_card, 2)
        self.body.addLayout(row)
        self.body.addStretch(1)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.new()

    # ---------------------------------------------------------------- data
    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> list[dict[str, Any]]:
        from app.auth import list_users

        return list_users()

    def _apply(self, users: list[dict[str, Any]]) -> None:
        self.table.set_rows(users or [])
        if self.edit_id is not None:
            self.table.select_key(self.edit_id, scroll=False)
        self.actions.sync()
        self._sync_toggle_label()

    def _is_current(self, item: dict[str, Any]) -> bool:
        return int(item.get("id") or 0) == int(self.context.user.id or 0)

    def _sync_toggle_label(self) -> None:
        item = self.table.current_row()
        active = bool(item.get("is_active")) if item else True
        from app.desktop.icons import icon

        self.toggle_button.setText(t("Disable user") if active else t("Enable user"))
        self.toggle_button.setIcon(icon("account-off" if active else "account-reactivate", None, 16))

    def _clicked(self) -> None:
        item = self.table.current_row()
        if item is not None:
            self.edit(int(item["id"]))

    # ---------------------------------------------------------------- form
    def new(self) -> None:
        self.edit_id = None
        self.form_card.set_title(t("Create User"))
        self.username.setText("")
        self.username.setReadOnly(False)
        self.password.clear()
        self.form.captions["password"].setText(t("Password"))
        set_combo_value(self.role, "viewer")
        self.active.setChecked(True)
        self.save_button.setText(t("Create User"))
        self.cancel_button.setVisible(False)
        self.table.clearSelection()

    def edit(self, user_id: int) -> None:
        from app.auth import get_user_record_by_id

        user = get_user_record_by_id(user_id)
        if user is None:
            self.form_flash.error(t("User not found."))
            return
        self.edit_id = user_id
        self.form_card.set_title(t("Edit User"))
        self.username.setText(str(user.get("username") or ""))
        self.username.setReadOnly(False)
        self.password.clear()
        self.form.captions["password"].setText(t("New password"))
        set_combo_value(self.role, user.get("role") or "viewer")
        self.active.setChecked(bool(user.get("is_active")))
        self.save_button.setText(t("Save Changes"))
        self.cancel_button.setVisible(True)
        self.form_flash.hide()

    def _generate(self) -> None:
        self.password.setText(generate_password())
        self.password.setEchoMode(QLineEdit.EchoMode.Normal)
        self.visibility_button.setText(t("Hide"))

    def _toggle_visibility(self) -> None:
        hidden = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.visibility_button.setText(t("Hide") if hidden else t("Show"))

    def save(self) -> None:
        from app.auth import create_user, rename_user, update_user

        role = str(combo_value(self.role) or "viewer")
        try:
            if self.edit_id is not None:
                if self.edit_id == self.context.user.id and (role != "admin" or not self.active.isChecked()):
                    raise ValueError("You cannot remove admin rights or disable the currently signed-in account.")
                if (role != "admin" or not self.active.isChecked()) and other_active_admins(self.edit_id) == 0:
                    raise ValueError(LAST_ADMIN_ERROR)
                username = self.username.text().strip()
                rename_user(self.edit_id, username)
                update_user(self.edit_id, role=role, is_active=self.active.isChecked(), password=self.password.text().strip() or None)
                if self.edit_id == self.context.user.id and self.context.user.username != username:
                    # The sidebar shows the signed-in account's name.
                    self.context.user = replace(self.context.user, username=username)
                    if self.context.main_window is not None:
                        self.context.main_window.sidebar.refresh_chrome()
                message = "User updated."
                self.edit(self.edit_id)
            else:
                if not self.username.text().strip() or not self.password.text():
                    raise ValueError("Username and password are required.")
                create_user(username=self.username.text().strip(), password=self.password.text(), role=role,
                            is_active=self.active.isChecked())
                message = "User created."
                self.new()
        except (ValueError, sqlite3.IntegrityError) as exc:
            self.form_flash.error(t(str(exc)))
            return
        self.form_flash.success(t(message))
        self.refresh()

    def _toggle(self, item: dict[str, Any]) -> None:
        from app.auth import set_user_active

        if item.get("is_active") and item.get("role") == "admin" and other_active_admins(int(item["id"])) == 0:
            self.page_flash.error(t(LAST_ADMIN_ERROR))
            return
        set_user_active(int(item["id"]), not bool(item.get("is_active")))
        self.page_flash.success(t("User status updated."))
        self.refresh()

    def _delete(self, item: dict[str, Any]) -> None:
        if self._is_current(item):
            self.page_flash.error(t("You cannot delete the currently signed-in account."))
            return
        if item.get("role") == "admin" and item.get("is_active") and other_active_admins(int(item["id"])) == 0:
            self.page_flash.error(t(LAST_ADMIN_ERROR))
            return
        if not confirm(self, t("Delete user"), tf("Delete user {username}?", username=item.get("username")), danger=True,
                       confirm_text=t("Delete")):
            return
        from app.auth import delete_user

        try:
            delete_user(int(item["id"]))
        except ValueError as exc:
            self.page_flash.error(t(str(exc)))
            return
        if self.edit_id == int(item["id"]):
            self.new()
        self.page_flash.success(t("User deleted."))
        self.refresh()
