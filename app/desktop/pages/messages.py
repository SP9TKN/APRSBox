"""Messages page (web: templates/messages.html)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.desktop.help import show_help
from app.desktop.i18n import t, tf
from app.desktop.icons import icon_pixmap
from app.desktop.tasks import SerialLoader
from app.desktop.theme import current_colors, mix
from app.desktop.widgets.common import (
    Badge,
    Card,
    FlashMessage,
    FormGrid,
    Page,
    button,
    checkbox,
    clear_layout,
    combo_value,
    confirm,
    icon_button,
    label,
    line_edit,
    set_combo_options,
    tone_color,
)

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,6}(-([0-9]|1[0-5]))?$")
GROUP_RE = re.compile(r"^[A-Z0-9]{1,9}$")
REFRESH_MS = 5000
HISTORY_WINDOW = 60  # newest messages shown at first; "Older messages" adds more
# The read-only "RF traffic" channel: every message frame heard or sent on RF (app.services.rf_message_log).
RF_CHANNEL_ID = "rf-traffic"
RF_CHANNEL_KIND = "rf_traffic"
RF_ORIGIN_LABELS = {
    "rx": ("Received", "neutral"),
    "own": ("Sent", "accent"),
    "digi": ("Repeated by my digipeater", "warn"),
    "aprsis_to_rf": ("From APRS-IS to RF", "warn"),
    "proxy": ("Sent by a KISS client", "warn"),
}
RF_KIND_LABELS = {"ack": "ACK", "rej": "REJ", "telemetry": "Telemetry", "bulletin": "Bulletin", "query": "Query"}


def normalize_callsign(value: Any) -> str:
    return str(value or "").strip().upper()


def validate_callsign(value: Any) -> bool:
    return bool(CALLSIGN_RE.fullmatch(normalize_callsign(value)))


def normalize_message_text(value: str, limit: int) -> str:
    text = re.sub(r"[\x00-\x1F\x7F]", "", str(value or ""))
    text = re.sub(r"[^\x20-\x7E]", "", text)
    return text[:limit]


def normalize_path(value: str) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"[\x00-\x1F\x7F]", "", text)
    text = re.sub(r"[^\x20-\x7E]", "", text)
    return text[:64]


def validate_target_groups(value: str) -> tuple[bool, list[str], str]:
    raw = str(value or "")
    if not raw.strip():
        return True, [], ""
    groups: list[str] = []
    for raw_group in raw.split(","):
        group = raw_group.strip().upper()
        if not group:
            return False, [], "Target groups cannot contain empty entries between commas."
        if not GROUP_RE.fullmatch(group):
            return False, [], "Target groups must contain 1-9 letters or digits, separated by commas."
        if group.startswith("BLN"):
            return False, [], "Bulletin addresses are configured separately from message target groups."
        if group not in groups:
            groups.append(group)
    return True, groups, ""


def mute_actions(callsign: Any) -> list[tuple[str, str]]:
    """Menu entries (label, muted entry) for one sender: the exact station and, with an SSID,
    the whole callsign - a bare callsign mutes all of its SSIDs."""
    sender = normalize_callsign(callsign)
    if not sender:
        return []
    base = sender.split("-", 1)[0]
    actions = []
    if base != sender:
        actions.append((tf("Mute sender {callsign}", callsign=sender), sender))
    actions.append((tf("Mute {callsign} (all SSIDs)", callsign=base), base))
    return actions


def popup_menu(menu: QMenu, global_pos: Any) -> None:
    menu.exec(global_pos)


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def band_label(band: Any) -> str:
    """'2m' -> '2 m', '70cm' -> '70 cm' (the interface band)."""
    value = str(band or "").strip()
    for unit in ("cm", "m"):
        if value.lower().endswith(unit) and value[: -len(unit)].strip().isdigit():
            return f"{value[: -len(unit)].strip()} {unit}"
    return value


def delivery_label(message: dict[str, Any]) -> str:
    state = str(message.get("delivery_state") or "")
    limit = int(message.get("tx_attempt_limit") or 0)
    if state == "queued":
        return t("Queued")
    if state == "sent":
        if limit > 0:
            return tf("Sent {attemptCount}/{attemptLimit}", attemptCount=int(message.get("tx_attempt_count") or 0), attemptLimit=limit)
        return t("Sent")
    if state == "acked":
        return "ACK"
    if state == "rejected":
        return t("Rejected (REJ)")
    if state == "failed":
        return t("No ACK") if limit > 0 else t("Failed")
    if state == "received":
        return t("Received")
    return state


class MessageBubble(QFrame):
    def __init__(self, message: dict[str, Any], conversation: dict[str, Any], on_retry: Any, parent: QWidget | None = None,
                 on_mute: Any = None) -> None:
        super().__init__(parent)
        colors = current_colors()
        rx = message.get("direction") == "rx"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        if conversation.get("kind") == "group":
            sender = normalize_callsign(message.get("sender"))
            caption = tf("You · {callsign}", callsign=sender) if not rx else sender
            layout.addWidget(label(caption, "h3", selectable=True))  # the callsign can be copied, like the text
        body = str(message.get("text") or "")
        text = label(body, None, wrap=True, selectable=True)
        # A wrapped QLabel asks for a narrow width; size it from the text instead.
        text.setMinimumWidth(min(text.fontMetrics().horizontalAdvance(body) + 4, 520))
        text.setMaximumWidth(520)
        layout.addWidget(text)
        meta = QHBoxLayout()
        meta.setSpacing(8)
        timestamp = parse_utc(message.get("timestamp"))
        meta.addWidget(label(timestamp.strftime("%H:%M UTC") if timestamp else str(message.get("timestamp") or ""), "small"))
        if not rx:
            state = str(message.get("delivery_state") or "")
            text_label = delivery_label(message)
            if state == "failed" and int(message.get("tx_attempt_limit") or 0) > 0:
                retry = QPushButton(text_label or t("No ACK"))
                retry.setProperty("variant", "danger")
                retry.setToolTip(str(message.get("failure_reason") or t("No ACK received. Click to retry.")))
                retry.setCursor(Qt.CursorShape.PointingHandCursor)
                retry.clicked.connect(lambda _checked=False, message_id=message.get("id"): on_retry(message_id))
                meta.addWidget(retry)
            else:
                tone = "ok" if state == "acked" else ("error" if state == "rejected" else "warn")
                badge = Badge(text_label or t("Sent"), tone)
                if message.get("failure_reason"):
                    badge.setToolTip(str(message.get("failure_reason")))
                meta.addWidget(badge)
        meta.addStretch(1)
        layout.addLayout(meta)
        accent = colors.color("accent")
        if rx:
            self._background = colors.color("panel-alt")
            self._border = colors.color("border")
        else:
            self._background = mix(accent, colors.color("panel"), 0.22 if colors.is_dark else 0.14)
            self._border = mix(accent, colors.color("panel"), 0.46 if colors.is_dark else 0.33)
        # Painted here rather than with a style sheet per bubble: polishing hundreds of style sheets
        # made a big conversation (CQ, 270 messages) take seconds to open.
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self._text = body
        self._on_mute = on_mute
        self.mute_actions = mute_actions(message.get("sender")) if rx and on_mute is not None else []
        if self.mute_actions:
            # Right-click on a received message: copy, or mute its sender.
            for widget in (self, *self.findChildren(QLabel)):
                widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                widget.customContextMenuRequested.connect(lambda pos, source=widget: self._show_menu(source, pos))

    def _show_menu(self, source: QWidget, pos: Any) -> None:
        selected = source.selectedText() if isinstance(source, QLabel) and source.hasSelectedText() else ""
        menu = QMenu(self)
        menu.addAction(t("Copy"), lambda: QApplication.clipboard().setText(selected or self._text))
        menu.addSeparator()
        for caption, target in self.mute_actions:
            menu.addAction(caption, lambda target=target: self._on_mute(target))
        popup_menu(menu, source.mapToGlobal(pos))

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class RfTrafficBubble(QFrame):
    """One copy of a message frame on RF: who to whom, the text, band, path. Read only."""

    def __init__(self, message: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        colors = current_colors()
        rx = message.get("direction") == "rx"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        caption = f"{normalize_callsign(message.get('sender'))} → {normalize_callsign(message.get('addressee'))}"
        layout.addWidget(label(caption, "h3", selectable=True))
        body = str(message.get("text") or "")
        text = label(body, None, wrap=True, selectable=True)
        text.setMinimumWidth(min(text.fontMetrics().horizontalAdvance(body) + 4, 520))
        text.setMaximumWidth(520)
        layout.addWidget(text)
        meta = QHBoxLayout()
        meta.setSpacing(8)
        timestamp = parse_utc(message.get("timestamp"))
        meta.addWidget(label(timestamp.strftime("%H:%M:%S UTC") if timestamp else str(message.get("timestamp") or ""), "small"))
        band = band_label(message.get("band")) or str(message.get("interface_name") or "")
        if band:
            band_badge = Badge(band, "neutral")
            band_badge.setToolTip(str(message.get("interface_name") or ""))
            meta.addWidget(band_badge)
        origin_text, origin_tone = RF_ORIGIN_LABELS.get(str(message.get("origin") or ""), ("", "neutral"))
        if origin_text:
            meta.addWidget(Badge(t(origin_text), origin_tone))
        kind = RF_KIND_LABELS.get(str(message.get("kind") or ""))
        if kind:
            meta.addWidget(Badge(t(kind), "neutral"))
        if message.get("message_number"):
            meta.addWidget(label(f"#{message.get('message_number')}", "small"))
        meta.addStretch(1)
        layout.addLayout(meta)
        route = tf("Path: {path}", path=str(message.get("path") or "-"))
        if message.get("relay"):
            route = f"{route} · {tf('put on RF by {relay} (from the internet)', relay=message.get('relay'))}"
        layout.addWidget(label(route, "small", wrap=True, selectable=True))
        accent = colors.color("accent")
        if rx:
            self._background = colors.color("panel-alt")
            self._border = colors.color("border")
        else:
            self._background = mix(accent, colors.color("panel"), 0.22 if colors.is_dark else 0.14)
            self._border = mix(accent, colors.color("panel"), 0.46 if colors.is_dark else 0.33)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class MessagesPage(Page):
    page_key = "messages"

    def build(self) -> None:
        self.view: dict[str, Any] = {}
        self.conversations: list[dict[str, Any]] = []
        self.active_id: str | None = None
        self.drafts: dict[str, str] = {}
        self.checked: set[str] = set()
        self.composer_limit = 67
        self.default_path = ""
        self._history_states: list[tuple[Any, Any, Any]] = []
        self._history_limit = HISTORY_WINDOW
        self._history_last_day = ""
        self._list_signature = ""
        # Not a conversation: never counted as unread or written to; checked and cleared like one (the user's choice).
        self.rf_channel: dict[str, Any] = {"id": RF_CHANNEL_ID, "callsign": t("RF traffic"), "kind": RF_CHANNEL_KIND,
                                           "messages": [], "total": 0, "unread_count": 0}

        shell = QSplitter(Qt.Orientation.Horizontal)
        shell.setChildrenCollapsible(False)
        # Conversation list ---------------------------------------------------
        sidebar = Card()
        top = QHBoxLayout()
        top.addWidget(label(t("Conversations"), "h2"))
        self.inbox_count = Badge("0 / 0", "neutral")
        self.inbox_count.setToolTip(t("Messages: all / new"))
        top.addWidget(self.inbox_count)
        top.addStretch(1)
        top.addWidget(icon_button("help-circle-outline", t("Help"), lambda: show_help(self, "application/messages")))
        sidebar.add(top)
        sidebar.add(label(t("Start new conversation"), "muted"))
        new_row = QHBoxLayout()
        self.new_callsign = line_edit("", placeholder="SP9XYZ-7")
        self.new_callsign.returnPressed.connect(self._start_conversation)
        new_row.addWidget(self.new_callsign, 1)
        new_row.addWidget(icon_button("chat-plus-outline", t("Start conversation"), self._start_conversation))
        sidebar.add(new_row)
        self.sidebar_flash = FlashMessage()
        sidebar.add(self.sidebar_flash)
        tools = QHBoxLayout()
        self.select_all = QCheckBox(t("Select all"))
        self.select_all.toggled.connect(self._toggle_all)
        tools.addWidget(self.select_all)
        tools.addStretch(1)
        self.delete_selected_button = icon_button("delete-sweep-outline", t("Delete selected"), self._delete_selected,
                                                  color=current_colors().color("danger"))
        tools.addWidget(self.delete_selected_button)
        sidebar.add(tools)
        self.list = QListWidget()
        self.list.setIconSize(QSize(18, 18))
        self.list.itemClicked.connect(self._conversation_clicked)
        self.list.itemChanged.connect(self._item_check_changed)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.setMinimumWidth(300)
        sidebar.add(self.list, 1)
        shell.addWidget(sidebar)
        # Thread ----------------------------------------------------------------
        thread = Card()
        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.thread_title = label(t("Select a conversation"), "h2", selectable=True)
        self.thread_subtitle = label("", "muted", wrap=True)
        titles.addWidget(self.thread_title)
        titles.addWidget(self.thread_subtitle)
        header.addLayout(titles, 1)
        self.group_badge = Badge(t("Group"), "accent")
        self.group_badge.hide()
        header.addWidget(self.group_badge, 0, Qt.AlignmentFlag.AlignTop)
        self.delete_conversation_button = icon_button("delete-outline", t("Delete conversation"), self._delete_active,
                                                      color=current_colors().color("danger"))
        header.addWidget(self.delete_conversation_button, 0, Qt.AlignmentFlag.AlignTop)
        thread.add(header)
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.history_host = QWidget()
        self.history_host.setObjectName("PageBody")
        self.history_layout = QVBoxLayout(self.history_host)
        self.history_layout.setContentsMargins(6, 6, 6, 6)
        self.history_layout.setSpacing(8)
        self.history_scroll.setWidget(self.history_host)
        self.history_scroll.setMinimumHeight(320)
        # Like a chat: the view sticks to the newest message until the user scrolls up, and sticks
        # again once they scroll back to the bottom.
        self._history_follow = True
        self._history_conversation_id = ""
        history_bar = self.history_scroll.verticalScrollBar()
        history_bar.rangeChanged.connect(self._on_history_range_changed)
        history_bar.actionTriggered.connect(lambda _action: QTimer.singleShot(0, self._history_scrolled_by_user))
        thread.add(self.history_scroll, 1)
        self.composer_caption = label("", "muted")
        thread.add(self.composer_caption)
        self.composer = QPlainTextEdit()
        self.composer.setFixedHeight(64)
        self.composer.textChanged.connect(self._composer_changed)
        self.composer.installEventFilter(self)  # Enter sends
        thread.add(self.composer)
        footer = QHBoxLayout()
        footer.addWidget(label(t("Path"), "muted"))
        self.path_edit = line_edit("", placeholder="WIDE2-1")
        self.path_edit.setMaximumWidth(200)
        self.path_edit.textEdited.connect(self._path_edited)
        footer.addWidget(self.path_edit)
        footer.addStretch(1)
        self.send_button = button(t("Send"), icon_name="send", on_click=self._send)
        footer.addWidget(self.send_button)
        thread.add(footer)
        self.composer_error = label("", "error", wrap=True)
        self.composer_error.hide()
        thread.add(self.composer_error)
        shell.addWidget(thread)
        shell.setStretchFactor(0, 2)
        shell.setStretchFactor(1, 5)
        shell.setMinimumHeight(560)
        self.body.addWidget(shell, 1)

        # Settings ---------------------------------------------------------------
        settings_card = Card(t("Message settings"))
        grid = FormGrid(3)
        self.default_path_combo = QComboBox()
        grid.add_field("default_path", t("Default path"), self.default_path_combo)
        self.rf_groups = line_edit("", placeholder="CQ, YAESU, LOCAL")
        self.is_groups = line_edit("", placeholder="CQ, YAESU, LOCAL")
        grid.add_field("target_groups", t("RF groups"), self.rf_groups)
        grid.add_field("aprsis_target_groups", t("APRS-IS groups"), self.is_groups)
        self.muted_senders = line_edit("", placeholder="SP9XYZ-7, SQ9ABC")
        self.muted_senders.setToolTip(t("CALL-SSID mutes one station, a callsign without SSID mutes all of its SSIDs. "
                                        "Remove a callsign to unmute it."))
        grid.add_field("muted_senders", t("Muted senders"), self.muted_senders)
        self.receive_any = checkbox(t("Receive messages for any SSID of my callsign"))
        grid.add_widget(self.receive_any)
        settings_card.add(grid)
        settings_actions = QHBoxLayout()
        settings_actions.addWidget(button(t("Save settings"), icon_name="content-save-outline", on_click=self._save_settings))
        settings_actions.addStretch(1)
        settings_card.add(settings_actions)
        self.settings_flash = FlashMessage()
        settings_card.add(self.settings_flash)
        self.body.addWidget(settings_card)
        self.loader = SerialLoader(self._load, self._apply, self)
        self.auto_refresh_ms = REFRESH_MS
        self._settings_loaded = False
        self._pending_callsign: str | None = None
        self._render_composer()

    # ---------------------------------------------------------------- loading
    def open_argument(self, argument: Any) -> None:
        if isinstance(argument, dict) and argument.get("callsign"):
            callsign = normalize_callsign(argument.get("callsign"))
            if validate_callsign(callsign):
                self._pending_callsign = callsign

    def refresh(self) -> None:
        self.loader.request()

    def _load(self) -> dict[str, Any]:
        from app.services.messages import get_messages_page_data
        from app.services.rf_message_log import list_rf_message_log

        view = get_messages_page_data()
        # The RF traffic channel: the newest copies, more once "Older messages" is clicked there.
        view["rf_traffic"] = list_rf_message_log(limit=self._history_limit if self.active_id == RF_CHANNEL_ID else HISTORY_WINDOW)
        return view

    def _apply(self, view: dict[str, Any]) -> None:
        self.view = view or {}
        self.composer_limit = int(self.view.get("composer_limit") or 67)
        self.default_path = str(self.view.get("default_path") or "")
        self.conversations = list(self.view.get("conversations") or [])
        rf_traffic = self.view.get("rf_traffic")
        if rf_traffic is not None:  # a view reloaded after an action has no channel: it stays as it was
            self.rf_channel["messages"] = list(rf_traffic.get("items") or [])
            self.rf_channel["total"] = int(rf_traffic.get("total") or 0)
        if not self._settings_loaded:
            self._settings_loaded = True
            settings = self.view.get("message_settings") or {}
            set_combo_options(self.default_path_combo, settings.get("path_options") or [], settings.get("default_path") or "")
            self.rf_groups.setText(", ".join(settings.get("target_groups") or []))
            self.is_groups.setText(", ".join(settings.get("aprsis_target_groups") or []))
            self.muted_senders.setText(", ".join(settings.get("muted_senders") or []))
            self.receive_any.setChecked(bool(settings.get("receive_any_ssid")))
        ids = {str(item.get("id")) for item in self.conversations}
        self.checked &= ids | {RF_CHANNEL_ID}
        if self.active_id not in ids and self.active_id != RF_CHANNEL_ID:
            unread = next((item for item in self.conversations if int(item.get("unread_count") or 0) > 0), None)
            chosen = unread or (self.conversations[0] if self.conversations else None)
            self.active_id = str(chosen.get("id")) if chosen else None
        if self._pending_callsign:
            callsign = self._pending_callsign
            self._pending_callsign = None
            self._create_conversation(callsign)
            return
        self._render()
        self._mark_active_read()

    # -------------------------------------------------------------- rendering
    def _active(self) -> dict[str, Any] | None:
        if self.active_id == RF_CHANNEL_ID:
            return self.rf_channel
        return next((item for item in self.conversations if str(item.get("id")) == self.active_id), None)

    def _render(self) -> None:
        self._render_count()
        self._render_list()
        self._render_thread()
        self._render_composer()

    def _render_count(self) -> None:
        total = sum(len(item.get("messages") or []) for item in self.conversations)
        unread = sum(
            1 for item in self.conversations for message in item.get("messages") or []
            if message.get("direction") == "rx" and message.get("unread")
        )
        self.inbox_count.set_badge(f"{total} / {unread}", "warn" if unread else "neutral")

    def _render_list(self) -> None:
        signature = repr([(item.get("id"), item.get("callsign"), item.get("unread_count"), item.get("heard_recently_state"),
                           item.get("last_activity_at"), str(item.get("id")) == self.active_id, str(item.get("id")) in self.checked)
                          for item in self.conversations]) + repr((self.active_id == RF_CHANNEL_ID, RF_CHANNEL_ID in self.checked))
        if signature == self._list_signature:
            return
        self._list_signature = signature
        self.list.blockSignals(True)
        self.list.clear()
        rf_item = QListWidgetItem(t("RF traffic"))
        rf_item.setIcon(icon_pixmap("access-point", current_colors().color("accent"), 18))
        rf_item.setToolTip(t("Every message heard or sent on RF (2 m, 70 cm), read only"))
        rf_item.setData(Qt.ItemDataRole.UserRole, RF_CHANNEL_ID)
        rf_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        rf_item.setCheckState(Qt.CheckState.Checked if RF_CHANNEL_ID in self.checked else Qt.CheckState.Unchecked)
        self.list.addItem(rf_item)
        if self.active_id == RF_CHANNEL_ID:
            rf_item.setSelected(True)
            self.list.setCurrentItem(rf_item)
        if not self.conversations:
            empty = QListWidgetItem(t("No conversations loaded."))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        colors = current_colors()
        for conversation in self.conversations:
            conversation_id = str(conversation.get("id"))
            is_active = conversation_id == self.active_id
            unread = 0 if is_active else int(conversation.get("unread_count") or 0)
            if conversation.get("kind") == "group":
                status = t("Group")
                status_icon = icon_pixmap("account-group-outline", colors.color("accent"), 18)
            elif conversation.get("recently_heard"):
                status = str(conversation.get("heard_recently_label") or t("Heard recently"))
                tone = {"fresh": "ok", "warn": "warn", "stale": "error"}.get(str(conversation.get("heard_recently_state")), "neutral")
                status_icon = icon_pixmap("antenna", tone_color(tone), 18)
            else:
                status = t("No recent heard-station entry")
                status_icon = icon_pixmap("antenna", colors.color("muted"), 18)
            state = tf("Unread ({count})", count=unread) if unread > 0 else t("Read")
            item = QListWidgetItem(f"{conversation.get('callsign')}    {state}")
            item.setIcon(status_icon)
            item.setToolTip(status)
            item.setData(Qt.ItemDataRole.UserRole, conversation_id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if conversation_id in self.checked else Qt.CheckState.Unchecked)
            if unread > 0:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(colors.color("warning"))
            self.list.addItem(item)
            if is_active:
                item.setSelected(True)
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.select_all.blockSignals(True)
        self.select_all.setChecked(self._all_checked())
        self.select_all.setEnabled(True)  # the RF traffic channel is always there
        self.select_all.blockSignals(False)
        self.delete_selected_button.setEnabled(bool(self.checked))

    def _render_thread(self) -> None:
        conversation = self._active()
        if conversation is None:
            self.thread_title.setText(t("Select a conversation"))
            self.thread_subtitle.setText("")
            self.group_badge.hide()
            self.delete_conversation_button.setEnabled(False)
            clear_layout(self.history_layout)
            self._history_states = []
            self._history_conversation_id = ""
            return
        is_rf = conversation.get("kind") == RF_CHANNEL_KIND
        self.delete_conversation_button.setEnabled(True)
        is_group = conversation.get("kind") == "group"
        self.thread_title.setText(str(conversation.get("callsign") or ""))
        self.group_badge.setVisible(is_group)
        if is_rf:
            self.thread_subtitle.setText(t("Every message heard or sent on RF (2 m, 70 cm) · read only, nothing is sent from here."))
        elif is_group:
            self.thread_subtitle.setText(t("Group conversation · messages are sent once without ACK."))
        elif conversation.get("recently_heard"):
            self.thread_subtitle.setText(str(conversation.get("heard_recently_label") or t("Heard recently")))
        else:
            self.thread_subtitle.setText(t("No recent heard-station entry"))
        messages = list(conversation.get("messages") or [])
        conversation_id = str(conversation.get("id"))
        # What the bubbles show - not the unread flag: marking a conversation read must not rebuild it.
        states = [(message.get("id"), message.get("delivery_state"), message.get("tx_attempt_count"))
                  for message in messages]
        conversation_changed = self._history_conversation_id != conversation_id
        if conversation_changed:
            self._history_limit = HISTORY_WINDOW
        elif states == self._history_states:
            return
        appended = (not conversation_changed and bool(self._history_states)
                    and states[:len(self._history_states)] == self._history_states)
        self._history_conversation_id = conversation_id
        if appended:
            # New messages only: add their bubbles, keep the others (CQ gets one every few minutes).
            new_messages = messages[len(self._history_states):]
            self._history_states = states
            for message in new_messages:
                self._add_bubble(message, conversation)
            if self._history_follow:
                self._scroll_history_to_newest()
            return
        self._history_states = states
        clear_layout(self.history_layout)
        self._history_last_day = ""
        if not messages:
            empty_text = t("No RF messages heard yet.") if is_rf else t("No messages in this conversation yet.")
            empty = label(f"{conversation.get('callsign')}\n{empty_text}", "muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_layout.addWidget(empty)
            self.history_layout.addStretch(1)
            return
        self.history_layout.addStretch(1)
        if is_rf:  # only the newest copies are loaded; the rest stay in the database
            hidden = max(0, int(conversation.get("total") or 0) - len(messages))
            shown = messages
        else:
            hidden = max(0, len(messages) - self._history_limit)
            shown = messages[hidden:]
        if hidden:
            # The newest messages first; a big conversation (CQ) took seconds to build in full.
            older = QPushButton(tf("Older messages ({count})", count=hidden))
            older.setProperty("variant", "secondary")
            older.clicked.connect(self._show_older_messages)
            self.history_layout.addWidget(older, 0, Qt.AlignmentFlag.AlignHCenter)
        for message in shown:
            self._add_bubble(message, conversation)
        if self._history_follow or conversation_changed:
            self._scroll_history_to_newest()

    def _add_bubble(self, message: dict[str, Any], conversation: dict[str, Any]) -> None:
        timestamp = parse_utc(message.get("timestamp"))
        day = timestamp.strftime("%Y.%m.%d UTC") if timestamp else ""
        if day and day != self._history_last_day:
            separator = label(day, "small")
            separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_layout.addWidget(separator)
            self._history_last_day = day
        row = QHBoxLayout()
        if conversation.get("kind") == RF_CHANNEL_KIND:
            bubble: QFrame = RfTrafficBubble(message)
        else:
            bubble = MessageBubble(message, conversation, self._retry, on_mute=self._mute_sender)
        if message.get("direction") == "rx":
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            row.addStretch(1)
            row.addWidget(bubble)
        self.history_layout.addLayout(row)

    def _show_older_messages(self) -> None:
        self._history_limit += HISTORY_WINDOW * 2
        self._history_states = []  # rebuild with the longer window
        self._history_follow = False
        self._render_thread()
        if self.active_id == RF_CHANNEL_ID:
            self.loader.request()  # the older copies come from the database
        QTimer.singleShot(0, lambda: self.history_scroll.verticalScrollBar().setValue(0))

    def _scroll_history_to_newest(self) -> None:
        """Bring the newest message into view and keep it there while the bubbles finish their
        word-wrapped layout - however long a big conversation takes on a slow PC."""
        self._history_follow = True
        bar = self.history_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()) if self._history_follow else None)

    def _on_history_range_changed(self, _minimum: int, maximum: int) -> None:
        if self._history_follow:
            self.history_scroll.verticalScrollBar().setValue(maximum)

    def _history_scrolled_by_user(self) -> None:
        # Wheel, drag or page keys: follow the newest message only while the user stays at the bottom.
        bar = self.history_scroll.verticalScrollBar()
        self._history_follow = bar.value() >= bar.maximum() - 40

    def activate(self, argument: Any = None) -> None:
        super().activate(argument)
        self._scroll_history_to_newest()  # opening Messages shows the newest message (the user's choice)

    def _render_composer(self) -> None:
        conversation = self._active()
        text = self.composer.toPlainText()
        self.composer_caption.setText(f"{t('Message text')} ({len(text)} / {self.composer_limit})")
        read_only = conversation is not None and conversation.get("kind") == RF_CHANNEL_KIND
        writable = conversation is not None and not read_only
        self.composer.setEnabled(writable)
        self.path_edit.setEnabled(writable)
        if read_only:
            self.composer.setPlaceholderText(t("RF traffic is read only: open a conversation to write."))
        else:
            self.composer.setPlaceholderText(f"{t('Type APRS message text')} ({t('Enter sends')})" if conversation
                                             else t("Select a conversation to enable the composer"))
        self.send_button.setEnabled(writable and bool(text.strip()) and len(text) <= self.composer_limit)

    # --------------------------------------------------------------- actions
    def _conversation_clicked(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(Qt.ItemDataRole.UserRole)
        if not conversation_id:
            return
        if self.active_id is not None:
            self.drafts[self.active_id] = self.composer.toPlainText()
        self.active_id = str(conversation_id)
        self.composer.blockSignals(True)
        self.composer.setPlainText(self.drafts.get(self.active_id, ""))
        self.composer.blockSignals(False)
        conversation = self._active()
        if conversation is not None and conversation.get("kind") != RF_CHANNEL_KIND:
            stored = str(self.context.ui_value(f"messages/path/{normalize_callsign(conversation.get('callsign'))}", "") or "")
            self.path_edit.setText(stored or self.default_path)
        self._list_signature = ""
        self._render()
        self._mark_active_read()

    def _mark_active_read(self) -> None:
        conversation = self._active()
        if conversation is None or int(conversation.get("unread_count") or 0) <= 0:
            return
        conversation_id = int(conversation["id"])
        from app.services.messages import mark_conversation_read

        mark_conversation_read(conversation_id)
        conversation["unread_count"] = 0
        for message in conversation.get("messages") or []:
            if message.get("direction") == "rx":
                message["unread"] = False
        self.context.notify_data_changed("messages")
        self._list_signature = ""
        self._render_count()
        self._render_list()

    def _item_check_changed(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(Qt.ItemDataRole.UserRole)
        if not conversation_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self.checked.add(str(conversation_id))
        else:
            self.checked.discard(str(conversation_id))
        self.delete_selected_button.setEnabled(bool(self.checked))
        self.select_all.blockSignals(True)
        self.select_all.setChecked(self._all_checked())
        self.select_all.blockSignals(False)

    def _all_checked(self) -> bool:
        return self.checked == {str(item.get("id")) for item in self.conversations} | {RF_CHANNEL_ID}

    def _toggle_all(self, checked: bool) -> None:
        self.checked = ({str(item.get("id")) for item in self.conversations} | {RF_CHANNEL_ID}) if checked else set()
        self._list_signature = ""
        self._render_list()

    def _start_conversation(self) -> None:
        callsign = normalize_callsign(self.new_callsign.text())
        if not validate_callsign(callsign):
            self.sidebar_flash.error(t("Destination callsign must be an AX.25/APRS callsign with optional SSID 0-15."))
            return
        self.sidebar_flash.hide()
        self._create_conversation(callsign)
        self.new_callsign.clear()

    def _create_conversation(self, callsign: str) -> None:
        from app.services.messages import create_or_update_conversation, get_messages_page_data

        try:
            conversation = create_or_update_conversation(callsign, path="")
        except ValueError as exc:
            self.sidebar_flash.error(t(str(exc)))
            return
        self.active_id = str(conversation.get("id") or "")
        self._apply(get_messages_page_data())

    def _composer_changed(self) -> None:
        text = self.composer.toPlainText()
        filtered = normalize_message_text(text, self.composer_limit)
        if filtered != text:
            cursor = self.composer.textCursor()
            position = cursor.position()
            self.composer.blockSignals(True)
            self.composer.setPlainText(filtered)
            cursor = self.composer.textCursor()
            cursor.setPosition(min(position, len(filtered)))
            self.composer.setTextCursor(cursor)
            self.composer.blockSignals(False)
            self.composer_error.setText(tf("Use printable ASCII only, max {limit} chars. National characters are blocked.", limit=self.composer_limit))
            self.composer_error.show()
        else:
            self.composer_error.hide()
        if self.active_id is not None:
            self.drafts[self.active_id] = self.composer.toPlainText()
        self._render_composer()

    def _path_edited(self, text: str) -> None:
        normalized = normalize_path(text)
        if normalized != text:
            self.path_edit.setText(normalized)
        conversation = self._active()
        if conversation is not None:
            self.context.set_ui_value(f"messages/path/{normalize_callsign(conversation.get('callsign'))}", normalized)

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt API
        # Enter sends, the way the Send button does (the user's choice). An APRS message is a
        # single line, so Shift+Enter does not add a line either.
        if (
            watched is self.composer
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.send_button.isEnabled():
                self._send()
            return True
        return super().eventFilter(watched, event)

    def _send(self) -> None:
        conversation = self._active()
        if conversation is None or conversation.get("kind") == RF_CHANNEL_KIND:
            return
        text = normalize_message_text(self.composer.toPlainText(), self.composer_limit)
        if not text.strip():
            return
        path = normalize_path(self.path_edit.text()) or self.default_path
        from app.services.messages import get_messages_page_data, queue_outgoing_message, update_conversation_path

        try:
            update_conversation_path(int(conversation["id"]), path)
            queue_outgoing_message(callsign=str(conversation.get("callsign") or ""), message_text=text, path=path)
        except ValueError as exc:
            self.composer_error.setText(t(str(exc)))
            self.composer_error.show()
            return
        self.drafts.pop(self.active_id or "", None)
        self.composer.blockSignals(True)
        self.composer.clear()
        self.composer.blockSignals(False)
        self._apply(get_messages_page_data())

    def _retry(self, message_id: Any) -> None:
        if not confirm(self, t("Are you sure?"), t("Repeat sending this APRS message?"), confirm_text=t("Send")):
            return
        from app.services.messages import get_messages_page_data, retry_failed_message

        try:
            retry_failed_message(int(message_id))
        except (TypeError, ValueError) as exc:
            self.composer_error.setText(t(str(exc)))
            self.composer_error.show()
            return
        self._apply(get_messages_page_data())

    def _list_menu(self, pos: Any) -> None:
        item = self.list.itemAt(pos)
        conversation_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        conversation = next((entry for entry in self.conversations if str(entry.get("id")) == str(conversation_id)), None)
        if conversation is None or conversation.get("kind") == "group":
            return  # a group is not a sender; mute its senders from their messages
        menu = QMenu(self)
        for caption, target in mute_actions(conversation.get("callsign")):
            menu.addAction(caption, lambda target=target: self._mute_sender(target))
        popup_menu(menu, self.list.viewport().mapToGlobal(pos))

    def _mute_sender(self, callsign: str) -> None:
        shown = callsign if "-" in callsign else tf("{callsign} (all SSIDs)", callsign=callsign)
        if not confirm(self, t("Mute sender"), tf(
                "New messages from {callsign} will not be saved or shown, and the old ones will be hidden. "
                "A private message to you still gets its ACK. Unmute it in Message settings, field Muted senders.",
                callsign=shown), confirm_text=t("Mute")):
            return
        from app.services.messages import get_messages_page_data, set_message_sender_muted

        try:
            muted = set_message_sender_muted(callsign, True)
        except ValueError as exc:
            self.sidebar_flash.error(t(str(exc)))
            return
        self.muted_senders.setText(", ".join(muted))
        self.sidebar_flash.success(tf("Muted: {callsign}", callsign=shown))
        self.context.notify_data_changed("messages")
        self._apply(get_messages_page_data())

    def _clear_rf_channel(self) -> None:
        from app.services.rf_message_log import clear_rf_message_log

        clear_rf_message_log()
        self.rf_channel["messages"], self.rf_channel["total"] = [], 0  # the channel stays and keeps collecting
        self._history_states = []

    def _delete_active(self) -> None:
        conversation = self._active()
        if conversation is None:
            return
        if conversation.get("kind") == RF_CHANNEL_KIND:
            if not confirm(self, t("Clear RF traffic"), tf("This will permanently delete the {count} messages of the RF traffic "
                                                            "channel. New ones keep coming in.", count=int(self.rf_channel["total"])),
                           danger=True, confirm_text=t("Delete")):
                return
            self._clear_rf_channel()
            self._list_signature = ""
            self._render()
            self.refresh()
            return
        callsign = str(conversation.get("callsign") or "")
        if not confirm(self, t("Are you sure?"), tf("This will permanently delete the conversation with {callsign} and all its messages.", callsign=callsign),
                       danger=True, confirm_text=t("Delete")):
            return
        from app.services.messages import delete_conversation, get_messages_page_data

        delete_conversation(int(conversation["id"]))
        self.active_id = None
        self.context.notify_data_changed("messages")
        self._apply(get_messages_page_data())

    def _delete_selected(self) -> None:
        if not self.checked:
            return
        selected = [item for item in self.conversations if str(item.get("id")) in self.checked]
        rf_selected = RF_CHANNEL_ID in self.checked
        message_count = sum(len(item.get("messages") or []) for item in selected)
        clear_all = bool(selected) and len(selected) == len(self.conversations)
        if not selected:
            description = t("This will permanently delete the messages of the RF traffic channel. New ones keep coming in.")
        elif clear_all:
            description = t("This will permanently delete all saved APRS conversations and their received and sent messages.")
        else:
            description = t("This will permanently delete the selected APRS conversations and their received and sent messages.")
        lines = [tf("{conversationCount} conversations · {messageCount} messages", conversationCount=len(selected),
                    messageCount=message_count)] if selected else []
        if rf_selected:
            lines.append(tf("RF traffic channel: {count} messages", count=int(self.rf_channel["total"])))
        title = t("Clear RF traffic") if not selected else (t("Clear inbox") if clear_all else t("Delete selected"))
        if not confirm(self, title, f"{description}\n\n" + "\n".join(lines), danger=True, confirm_text=t("Delete")):
            return
        from app.services.messages import clear_message_inbox, delete_conversations, get_messages_page_data

        if clear_all:
            clear_message_inbox()
        elif selected:
            delete_conversations([int(item["id"]) for item in selected])
        if rf_selected:
            self._clear_rf_channel()
        self.checked = set()
        self.context.notify_data_changed("messages")
        self._apply(get_messages_page_data())

    def _save_settings(self) -> None:
        valid_rf, rf_groups, rf_error = validate_target_groups(self.rf_groups.text())
        valid_is, is_groups, is_error = validate_target_groups(self.is_groups.text())
        if not valid_rf or not valid_is:
            self.settings_flash.error(t(rf_error or is_error))
            return
        from app.services.messages import save_message_settings

        try:
            settings = save_message_settings(
                {
                    "default_path": combo_value(self.default_path_combo) or "",
                    "receive_any_ssid": self.receive_any.isChecked(),
                    "target_groups": rf_groups,
                    "aprsis_target_groups": is_groups,
                    "muted_senders": self.muted_senders.text(),
                }
            )
        except ValueError as exc:
            self.settings_flash.error(t(str(exc)))
            return
        self.rf_groups.setText(", ".join(settings.get("target_groups") or rf_groups))
        self.is_groups.setText(", ".join(settings.get("aprsis_target_groups") or is_groups))
        self.muted_senders.setText(", ".join(settings.get("muted_senders") or []))
        self.context.notify_data_changed("messages")
        self.settings_flash.success(t("Message settings saved."))
        self.refresh()
