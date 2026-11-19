"""WX page (web: templates/wx.html)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QWidget

from app.datetime_utils import format_display_datetime
from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.pages.station import station_form_options
from app.desktop.tasks import SerialLoader, run_async
from app.desktop.widgets.common import (
    Badge,
    Card,
    FlashMessage,
    FormGrid,
    Page,
    button,
    checkbox,
    combo_value,
    confirm,
    form_flag,
    icon_button,
    label,
    line_edit,
    set_combo_options,
    set_combo_value,
    spin,
)
from app.desktop.widgets.crud import RowActionBar, tx_log_table
from app.desktop.widgets.pickers import LocationPickerDialog
from app.desktop.widgets.table import Column, DataTable

ALL_ACTIVE = "__ALL_ACTIVE__"
DIRECT_MINUTES = (5, 10, 15, 30, 45, 60)
ROUTED_MINUTES = (15, 39, 45, 60)
STATUS_TONES = {"status-running": "ok", "status-unknown": "neutral", "status-stopped": "error"}


def path_allows_direct_intervals(path: str) -> bool:
    return str(path or "").strip().upper() in {"", "RFONLY"}


def interval_options(path: str) -> list[tuple[int, str]]:
    """Web: wx.html renderIntervalOptions (direct paths allow shorter intervals)."""
    minutes = DIRECT_MINUTES if path_allows_direct_intervals(path) else ROUTED_MINUTES
    return [(value * 60, f"{value}m") for value in minutes]


class MappingGrid(QWidget):
    """Editable WX parameter -> source mapping rows (web: .wx-mapping-table-edit).

    Background refreshes update values, status and cache in place; the source and
    identifier inputs are only rebuilt when the parameter list itself changes, so
    typing is never interrupted.
    """

    HEADERS = ("Parameter", "Source", "Identifier", "Raw value", "Normalized", "Status", "Cache", "")

    def __init__(self, page: "WxPage", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page = page
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(6)
        self.rows: dict[str, dict[str, Any]] = {}
        self.widgets: dict[str, dict[str, Any]] = {}
        self._names: list[str] = []
        self._editable: bool | None = None
        self._reload_inputs = True

    @property
    def inputs(self) -> dict[str, tuple[QComboBox, QLineEdit]]:
        return {name: (widgets["source"], widgets["identifier"]) for name, widgets in self.widgets.items()}

    def reload_inputs(self) -> None:
        """Take source/identifier from the database on the next refresh (after a save)."""
        self._reload_inputs = True

    def set_rows(self, rows: list[dict[str, Any]], *, editable: bool) -> None:
        names = [str(row.get("parameter_name")) for row in rows]
        self.rows = {name: row for name, row in zip(names, rows)}
        if names != self._names or editable != self._editable:
            self._build(rows, editable)
            self._names, self._editable = names, editable
        else:
            for name, row in zip(names, rows):
                self._update(name, row)
        self._reload_inputs = False

    def _build(self, rows: list[dict[str, Any]], editable: bool) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.widgets = {}
        for column, header in enumerate(self.HEADERS):
            if header:
                self.grid.addWidget(label(t(header), "h3"), 0, column)
        for index, row in enumerate(rows, start=1):
            name = str(row.get("parameter_name"))
            caption = QWidget()
            caption_layout = QHBoxLayout(caption)
            caption_layout.setContentsMargins(0, 0, 0, 0)
            caption_box = label(f"{t(row.get('label') or name)}", None)
            caption_box.setStyleSheet("font-weight: 700;")
            caption_box.setToolTip(str(row.get("description") or ""))
            caption_layout.addWidget(caption_box)
            caption_layout.addWidget(label(f"{row.get('aprs_field') or ''} / {row.get('canonical_unit') or ''}", "small"))
            caption_layout.addStretch(1)
            self.grid.addWidget(caption, index, 0)
            source = QComboBox()
            set_combo_options(source, row.get("source_options") or [], str(row.get("source_id") or ""))
            identifier = line_edit(row.get("identifier") or "", placeholder=t("Entity ID / idx"))
            source.setEnabled(editable)
            identifier.setEnabled(editable)
            self.grid.addWidget(source, index, 1)
            self.grid.addWidget(identifier, index, 2)
            raw = label("", "mono")
            normalized = label("", "mono")
            status = Badge("", "neutral")
            cache = label("", "small")
            self.grid.addWidget(raw, index, 3)
            self.grid.addWidget(normalized, index, 4)
            self.grid.addWidget(status, index, 5, Qt.AlignmentFlag.AlignLeft)
            self.grid.addWidget(cache, index, 6)
            if editable:
                test = icon_button("test-tube-empty", t("Run test read"), lambda value=name: self.page.test_mapping(value))
                self.grid.addWidget(test, index, 7)
            self.widgets[name] = {"source": source, "identifier": identifier, "raw": raw, "normalized": normalized,
                                  "status": status, "cache": cache}
            self._update_values(name, row)
        for column, stretch in enumerate((2, 2, 2, 1, 1)):
            self.grid.setColumnStretch(column, stretch)

    def _update(self, name: str, row: dict[str, Any]) -> None:
        widgets = self.widgets[name]
        source: QComboBox = widgets["source"]
        options = list(row.get("source_options") or [])
        wanted = [(str(option.get("value")), t(str(option.get("label") or ""))) for option in options]
        present = [(str(source.itemData(index)), source.itemText(index)) for index in range(source.count())]
        selected = str(row.get("source_id") or "") if self._reload_inputs else str(combo_value(source) or "")
        if wanted != present:
            set_combo_options(source, options, selected)
        elif self._reload_inputs:
            set_combo_value(source, selected)
        if self._reload_inputs:
            widgets["identifier"].setText(str(row.get("identifier") or ""))
        self._update_values(name, row)

    def _update_values(self, name: str, row: dict[str, Any]) -> None:
        widgets = self.widgets[name]
        widgets["raw"].setText(str(row.get("raw_value_preview") or "-"))
        widgets["normalized"].setText(str(row.get("normalized_value_preview") or "-"))
        status: Badge = widgets["status"]
        status.set_badge(t(str(row.get("status_label") or "")), STATUS_TONES.get(str(row.get("status_class") or ""), "neutral"))
        status.setToolTip(str(row.get("cache_last_error") or ""))
        origin = str(row.get("cache_origin_label") or "-")
        info = str(row.get("cache_info") or "")
        widgets["cache"].setText(f"{origin}\n{info}".strip())

    def payload(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, (source, identifier) in self.inputs.items():
            row = self.rows.get(name) or {}
            result[name] = {
                "source_id": str(combo_value(source) or "").strip(),
                "identifier": identifier.text().strip(),
                "selector_kind": str(row.get("selector_kind") or "state").strip(),
                "selector_name": str(row.get("selector_name") or "").strip(),
                "unit_override": str(row.get("unit_override") or "").strip(),
                "cache_max_age_s": str(row.get("cache_max_age_s") or "").strip(),
            }
        return result


class WxPage(Page):
    page_key = "wx"

    def build(self) -> None:
        self.view: dict[str, Any] = {}
        self.source_id: int | None = None
        self._config_loaded = False
        manage = self.can_edit

        # Global configuration -------------------------------------------------
        config_card = Card(t("Global WX configuration"))
        config_card.add_action(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/wx")))
        self.config_flash = FlashMessage()
        config_card.add(self.config_flash)
        grid = FormGrid(3)
        self.callsign = line_edit("", read_only=True)
        grid.add_field("callsign", t("Callsign"), self.callsign)
        self.ssid = QComboBox()
        grid.add_field("ssid", t("WX SSID"), self.ssid)
        self.interface = QComboBox()
        grid.add_field("beacon_interface_id", t("Interface"), self.interface)
        self.path = line_edit("", placeholder="WIDE2-2")
        self.path.textEdited.connect(self._path_edited)
        grid.add_field("path", t("Path"), self.path)
        self.latitude = line_edit("")
        self.longitude = line_edit("")
        grid.add_field("latitude", t("Latitude"), self.latitude)
        grid.add_field("longitude", t("Longitude"), self.longitude)
        self.interval = QComboBox()
        grid.add_field("refresh_interval_s", t("Refresh / TX interval"), self.interval)
        self.cache_age = spin(900, 1, 86400, suffix=" s")
        grid.add_field("default_cache_max_age_s", t("Default max cache age (s)"), self.cache_age)
        location = button(t("Get location"), variant="secondary", icon_name="map-marker-plus-outline", on_click=self._pick_location)
        grid.add_widget(location, span=1)
        self.allow_cache = checkbox(t("Allow cached values on failure"))
        grid.add_widget(self.allow_cache)
        self.enabled = checkbox(t("Enable WX"))
        grid.add_widget(self.enabled)
        config_card.add(grid)
        actions = QHBoxLayout()
        actions.addWidget(button(t("Save WX configuration"), icon_name="content-save-outline", on_click=self._save_config))
        actions.addWidget(button(t("Refresh now"), variant="secondary", icon_name="refresh", on_click=self._refresh_now))
        actions.addWidget(button(t("Send now"), variant="secondary", icon_name="send-outline", on_click=self._send_now))
        actions.addStretch(1)
        config_card.add(actions)
        config_card.setEnabled(manage)
        self.body.addWidget(config_card)

        # Mapping ---------------------------------------------------------------
        mapping_card = Card(t("WX data mapping"))
        self.mapping_flash = FlashMessage()
        mapping_card.add(self.mapping_flash)
        self.no_sources = label(t("No WX data sources are configured yet.") + " " + t("Add a source below") + ".", "warning", wrap=True)
        mapping_card.add(self.no_sources)
        mapping_card.add(label(t("Required parameters"), "h3"))
        self.required = MappingGrid(self)
        mapping_card.add(self.required)
        mapping_card.add(label(t("Optional parameters"), "h3"))
        self.optional = MappingGrid(self)
        mapping_card.add(self.optional)
        if manage:
            save_row = QHBoxLayout()
            save_row.addWidget(button(t("Save mappings"), icon_name="content-save-outline", on_click=self._save_mappings))
            save_row.addStretch(1)
            mapping_card.add(save_row)
        self.body.addWidget(mapping_card)

        # Sources -----------------------------------------------------------------
        sources_card = Card(t("WX data sources"))
        self.sources_flash = FlashMessage()
        sources_card.add(self.sources_flash)
        self.sources_table = DataTable(
            [
                Column("name", t("Name"), width=160, bold=lambda row: True),
                Column("source_type_label", t("Type"), width=130, display=lambda row: t(row.get("source_type_label") or "")),
                Column("base_url", t("Base URL"), stretch=True, mono=True),
                Column("auth_type", t("Auth"), width=80),
                Column("timeout_s", t("Timeout"), width=80, display=lambda row: f"{row.get('timeout_s')}s"),
                Column("verify_tls", t("TLS"), width=80, display=lambda row: t("Verify") if row.get("verify_tls") else t("Ignore")),
                Column("last_test", t("Last test"), width=200, display=lambda row: str(row.get("last_test_status_label") or "-"),
                       secondary=lambda row: " · ".join(part for part in (
                           format_display_datetime(row.get("last_test_at"), use_utc=True) if row.get("last_test_at") else "",
                           str(row.get("last_test_error") or "")) if part),
                       tooltip=lambda row: str(row.get("last_test_error") or "")),
            ],
            row_height=42,
            sortable=False,
        )
        self.sources_table.set_empty_text(t("No WX sources configured."))
        self.sources_table.setMinimumHeight(150)
        sources_card.add(self.sources_table)
        self.source_actions = RowActionBar(self.sources_table)
        if manage:
            self.sources_table.row_activated.connect(self._edit_source)
            self.source_actions.add(t("Run test read"), "test-tube", self._test_source)
            self.source_actions.add(t("Discover source"), "magnify", self._discover_source)
            self.source_actions.add(t("Edit source"), "pencil", self._edit_source)
            self.source_actions.add(t("Delete source"), "delete-outline", self._delete_source, variant="danger")
        sources_card.add(self.source_actions)
        if manage:
            source_form = FormGrid(3)
            self.s_name = line_edit("")
            source_form.add_field("name", t("Source name"), self.s_name)
            self.s_type = QComboBox()
            source_form.add_field("source_type", t("Source type"), self.s_type)
            self.s_auth = QComboBox()
            self.s_auth.currentIndexChanged.connect(lambda _index: self._auth_changed())
            source_form.add_field("auth_type", t("Auth type"), self.s_auth)
            self.s_url = line_edit("", placeholder="http://127.0.0.1:8123")
            source_form.add_field("base_url", t("Base URL"), self.s_url, span=3)
            self.s_token = line_edit("")
            source_form.add_field("token", t("Bearer token"), self.s_token)
            self.s_username = line_edit("")
            source_form.add_field("username", t("Username"), self.s_username)
            self.s_password = line_edit("")
            self.s_password.setEchoMode(QLineEdit.EchoMode.Password)
            source_form.add_field("password", t("Password"), self.s_password)
            self.s_timeout = spin(5, 1, 60, suffix=" s")
            source_form.add_field("timeout_s", t("Timeout (s)"), self.s_timeout)
            self.s_verify = checkbox(t("Verify TLS certificate"), True)
            source_form.add_widget(self.s_verify, span=1)
            self.s_enabled = checkbox(t("Enable source"), True)
            source_form.add_widget(self.s_enabled, span=1)
            self.source_form = source_form
            self.source_form_title = label(t("Add source"), "h3")
            sources_card.add(self.source_form_title)
            sources_card.add(source_form)
            source_buttons = QHBoxLayout()
            source_buttons.addWidget(button(t("Save source"), icon_name="content-save-outline", on_click=self._save_source))
            source_buttons.addWidget(button(t("Reset form"), variant="secondary", on_click=self._new_source))
            source_buttons.addStretch(1)
            sources_card.add(source_buttons)
        self.body.addWidget(sources_card)

        # Discovery ---------------------------------------------------------------
        self.discovery_card = Card(t("Discovery preview"))
        self.discovery_table = DataTable(
            [
                Column("identifier", t("Identifier"), width=240, mono=True),
                Column("label", t("Label"), stretch=True),
                Column("selector_hint", t("Default selector"), width=150, mono=True),
                Column("value_preview", t("Value preview"), width=150, mono=True),
                Column("unit", t("Unit"), width=80, display=lambda row: row.get("unit") or "-"),
            ],
        )
        self.discovery_table.set_empty_text(t("No items discovered."))
        self.discovery_table.setMinimumHeight(220)
        self.discovery_table.row_activated.connect(self._copy_identifier)
        self.discovery_card.add(self.discovery_table)
        self.discovery_card.add(label(t("Double-click an item to copy its identifier."), "small"))
        self.discovery_card.hide()
        self.body.addWidget(self.discovery_card)

        # TX log ------------------------------------------------------------------
        log_card = Card(t("WX TX Log"))
        self.log_table = tx_log_table(t("No WX outbound jobs yet."))
        log_card.add(self.log_table)
        self.body.addWidget(log_card)
        self.body.addStretch(1)

        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = 15000

    # --------------------------------------------------------------- loading
    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.services.wx import get_wx_page_data

        data = get_wx_page_data()
        data["interface_options"] = station_form_options()["interface_options"]
        return data

    def _apply(self, view: dict[str, Any]) -> None:
        self.view = view or {}
        config = self.view.get("wx_config") or {}
        if not self._config_loaded:
            self._config_loaded = True
            self._load_config(config)
            if self.can_edit:
                set_combo_options(self.s_type, self.view.get("wx_source_type_options") or [], "home_assistant")
                set_combo_options(self.s_auth, self.view.get("wx_auth_type_options") or [], "none")
                self._new_source()
        self.callsign.setText(str(config.get("callsign") or ""))
        has_sources = bool(self.view.get("wx_has_sources"))
        self.no_sources.setVisible(not has_sources)
        self.required.set_rows(list(self.view.get("wx_required_mappings") or []), editable=self.can_edit)
        self.optional.set_rows(list(self.view.get("wx_optional_mappings") or []), editable=self.can_edit)
        self.sources_table.set_rows(list(self.view.get("wx_sources") or []))
        self.source_actions.sync()
        self.log_table.set_rows(list(self.view.get("wx_tx_log_rows") or []))

    def _load_config(self, config: dict[str, Any]) -> None:
        self.ssid.clear()
        for option in config.get("ssid_options") or []:
            text = t(option.get("label") or "")
            if option.get("reason"):
                text += f" - {t(option.get('reason'))}"
            self.ssid.addItem(text, option.get("value"))
            if option.get("disabled"):
                item = self.ssid.model().item(self.ssid.count() - 1)
                if item is not None:
                    item.setEnabled(False)
        set_combo_value(self.ssid, str(config.get("ssid") or ""))
        selected = ALL_ACTIVE if config.get("beacon_tx_scope") == "all_active" else (
            str(config.get("beacon_interface_id")) if config.get("beacon_interface_id") is not None else "")
        set_combo_options(self.interface, self.view.get("interface_options") or [], selected)
        self.path.setText(str(config.get("path") or ""))
        self.latitude.setText(str(config.get("latitude") or ""))
        self.longitude.setText(str(config.get("longitude") or ""))
        self._render_intervals(int(config.get("refresh_interval_s") or 300))
        self.allow_cache.setChecked(bool(config.get("allow_cache_fallback")))
        self.cache_age.setValue(int(config.get("default_cache_max_age_s") or 900))
        self.enabled.setChecked(bool(config.get("enabled")))

    def _render_intervals(self, selected_s: int | None = None) -> None:
        previous = selected_s if selected_s is not None else combo_value(self.interval)
        options = interval_options(self.path.text())
        values = {value for value, _label in options}
        set_combo_options(self.interval, options, previous if previous in values else options[0][0], translate=False)

    def _path_edited(self, text: str) -> None:
        upper = text.upper()
        if upper != text:
            position = self.path.cursorPosition()
            self.path.setText(upper)
            self.path.setCursorPosition(position)
        self._render_intervals()

    def _pick_location(self) -> None:
        picked = LocationPickerDialog.pick(self, self.latitude.text(), self.longitude.text())
        if picked is not None:
            self.latitude.setText(f"{picked[0]:.5f}")
            self.longitude.setText(f"{picked[1]:.5f}")

    # --------------------------------------------------------------- config
    def _config_payload(self) -> dict[str, Any]:
        return {
            "enabled": form_flag(self.enabled.isChecked()),
            "ssid": str(combo_value(self.ssid) or "").strip(),
            "beacon_interface_id": str(combo_value(self.interface) or "").strip(),
            "path": self.path.text().strip(),
            "latitude": self.latitude.text().strip(),
            "longitude": self.longitude.text().strip(),
            "refresh_interval_s": str(combo_value(self.interval) or "300"),
            "allow_cache_fallback": form_flag(self.allow_cache.isChecked()),
            "default_cache_max_age_s": str(self.cache_age.value()),
        }

    def _save_config(self) -> bool:
        from app.services.wx import safe_save_wx_config

        success, error = safe_save_wx_config(self._config_payload())
        if not success:
            self.config_flash.error(t(error or "Failed to save WX configuration."))
            return False
        self.config_flash.success(t("WX configuration saved."))
        self._config_loaded = False
        self.refresh()
        return True

    def _refresh_now(self) -> None:
        from app.services.wx import safe_refresh_wx_runtime

        self.config_flash.info(t("Working..."))

        def done(result: tuple[bool, Any, str | None]) -> None:
            success, _payload, error = result
            if success:
                self.config_flash.success(t("WX refresh completed."))
            else:
                self.config_flash.error(t(error or "WX refresh failed."))
            self.refresh()

        run_async(lambda: safe_refresh_wx_runtime(trigger="manual"), done, lambda exc: self.config_flash.error(str(exc)), owner=self)

    def _send_now(self) -> None:
        if not confirm(self, t("Are you sure?"), t("Are you sure you want to send WX now?"), confirm_text=t("Send now")):
            return
        from app.services.wx import safe_enqueue_wx_outbound, safe_refresh_wx_runtime, safe_save_wx_config

        success, error = safe_save_wx_config(self._config_payload())
        if not success:
            self.config_flash.error(t(error or "Failed to save WX configuration."))
            return
        self.config_flash.info(t("Working..."))

        def work() -> tuple[bool, str]:
            refreshed, _payload, refresh_error = safe_refresh_wx_runtime(trigger="manual-send")
            if not refreshed:
                return False, str(refresh_error or "WX refresh failed.")
            return safe_enqueue_wx_outbound(trigger="manual")

        def done(result: tuple[bool, str]) -> None:
            queued, message = result
            (self.config_flash.success if queued else self.config_flash.error)(t(message))
            self.refresh()

        run_async(work, done, lambda exc: self.config_flash.error(str(exc)), owner=self)

    # -------------------------------------------------------------- mapping
    def _save_mappings(self) -> None:
        from app.services.wx import safe_save_wx_mappings

        payload = {**self.required.payload(), **self.optional.payload()}
        success, error = safe_save_wx_mappings(payload)
        if not success:
            self.mapping_flash.error(t(error or "Failed to save WX mappings."))
            return
        self.mapping_flash.success(t("WX mappings saved."))
        self.required.reload_inputs()
        self.optional.reload_inputs()
        self.refresh()

    def test_mapping(self, parameter_name: str) -> None:
        from app.services.wx import refresh_single_wx_mapping

        self.mapping_flash.info(t("Working..."))

        def work() -> tuple[bool, str]:
            try:
                result = refresh_single_wx_mapping(parameter_name, trigger="manual-test")
            except ValueError as exc:
                return False, str(exc)
            row = (result.get("rows") or [{}])[0]
            status = str(row.get("status") or "").upper()
            if status in {"LIVE", "CACHED"}:
                return True, f"WX mapping {parameter_name} refreshed with status {status}."
            return False, str(row.get("last_error") or f"WX mapping {parameter_name} refresh finished with status {status or 'UNKNOWN'}.")

        def done(result: tuple[bool, str]) -> None:
            success, message = result
            (self.mapping_flash.success if success else self.mapping_flash.error)(t(message))
            self.refresh()

        run_async(work, done, lambda exc: self.mapping_flash.error(str(exc)), owner=self)

    # -------------------------------------------------------------- sources
    def _auth_changed(self) -> None:
        auth = str(combo_value(self.s_auth) or "none")
        self.source_form.set_row_visible("token", auth == "bearer")
        self.source_form.set_row_visible("username", auth == "basic")
        self.source_form.set_row_visible("password", auth == "basic")

    def _fill_source(self, form: dict[str, Any]) -> None:
        self.source_id = int(form["id"]) if form.get("id") else None
        self.source_form_title.setText(t("Edit source") if self.source_id else t("Add source"))
        self.s_name.setText(str(form.get("name") or ""))
        set_combo_value(self.s_type, form.get("source_type") or "home_assistant")
        set_combo_value(self.s_auth, form.get("auth_type") or "none")
        self.s_url.setText(str(form.get("base_url") or ""))
        self.s_token.setText(str(form.get("token") or ""))
        self.s_username.setText(str(form.get("username") or ""))
        self.s_password.setText(str(form.get("password") or ""))
        self.s_timeout.setValue(int(form.get("timeout_s") or 5))
        self.s_verify.setChecked(bool(form.get("verify_tls", True)))
        self.s_enabled.setChecked(bool(form.get("enabled", True)))
        self._auth_changed()

    def _new_source(self) -> None:
        self._fill_source({"source_type": "home_assistant", "auth_type": "none", "timeout_s": 5, "verify_tls": True, "enabled": True})

    def _edit_source(self, row: dict[str, Any]) -> None:
        from app.services.wx import get_wx_page_data

        view = get_wx_page_data(edit_source_id=int(row["id"]))
        self._fill_source(view.get("wx_source_form") or {})

    def _save_source(self) -> None:
        from app.services.wx import safe_save_wx_source

        success, error, _saved = safe_save_wx_source(
            {
                "name": self.s_name.text().strip(),
                "source_type": str(combo_value(self.s_type) or "home_assistant"),
                "base_url": self.s_url.text().strip(),
                "auth_type": str(combo_value(self.s_auth) or "none"),
                "token": self.s_token.text().strip(),
                "username": self.s_username.text(),
                "password": self.s_password.text(),
                "timeout_s": str(self.s_timeout.value()),
                "verify_tls": form_flag(self.s_verify.isChecked()),
                "enabled": form_flag(self.s_enabled.isChecked()),
            },
            source_id=self.source_id,
        )
        if not success:
            self.sources_flash.error(t(error or "Failed to save WX source."))
            return
        self.sources_flash.success(t("WX source saved."))
        self._new_source()
        self.required.reload_inputs()
        self.optional.reload_inputs()
        self.refresh()

    def _delete_source(self, row: dict[str, Any]) -> None:
        if not confirm(self, t("Are you sure?"), t("Delete this WX source?") + f"\n\n{row.get('name')}", danger=True,
                       confirm_text=t("Delete")):
            return
        from app.services.wx import delete_wx_source

        delete_wx_source(int(row["id"]))
        if self.source_id == int(row["id"]):
            self._new_source()
        self.sources_flash.success(t("WX source deleted."))
        self.refresh()

    def _test_source(self, row: dict[str, Any]) -> None:
        from app.services.wx import test_wx_source_connection

        self.sources_flash.info(t("Working..."))

        def done(result: dict[str, Any]) -> None:
            if result.get("ok"):
                self.sources_flash.success(t("WX source connection succeeded."))
            else:
                self.sources_flash.error(t(str(result.get("error") or "WX source connection failed.")))
            self.refresh()

        run_async(lambda: test_wx_source_connection(int(row["id"])), done, lambda exc: self.sources_flash.error(str(exc)), owner=self)

    def _discover_source(self, row: dict[str, Any]) -> None:
        from app.services.wx import discover_wx_source_items

        self.sources_flash.info(t("Working..."))

        def done(result: dict[str, Any]) -> None:
            if not result.get("ok"):
                self.sources_flash.error(t(str(result.get("error") or "WX source discovery failed.")))
                return
            self.sources_flash.success(t("WX source discovery completed."))
            name = str((result.get("source") or {}).get("name") or row.get("name") or "")
            self.discovery_card.set_title(t("Discovery preview"), tf("Discovered items from {name}.", name=name))
            self.discovery_table.set_rows(list(result.get("items") or []))
            self.discovery_card.show()

        run_async(lambda: discover_wx_source_items(int(row["id"])), done, lambda exc: self.sources_flash.error(str(exc)), owner=self)

    def _copy_identifier(self, row: dict[str, Any]) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(str(row.get("identifier") or ""))
        self.sources_flash.success(tf("Copied: {value}", value=row.get("identifier") or ""))

