"""Help viewer (web: partials/help_modal.html + static/js/help-viewer.js)."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from app.config import settings
from app.desktop.i18n import current_language, t

HELP_ROOT = Path(settings.repo_root) / "help"


def _sanitize(value: str | None, *, require_md: bool) -> PurePosixPath | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    if require_md and path.suffix.lower() != ".md":
        return None
    if not require_md and path.suffix:
        return None
    return path


def read_help(page: str | None = None, path: str | None = None, language: str | None = None) -> tuple[str, str] | None:
    """Same lookup as pages._read_help_markdown: page.<lang>.md, then English."""
    candidates: list[PurePosixPath] = []
    if path:
        relative = _sanitize(path, require_md=True)
        if relative is not None:
            candidates.append(relative)
    else:
        page_path = _sanitize(page, require_md=False)
        if page_path is None:
            return None
        for code in (language or current_language(), "en"):
            candidate = page_path.parent / f"{page_path.name}.{code}.md"
            if candidate not in candidates:
                candidates.append(candidate)
    root = HELP_ROOT.resolve()
    for relative in candidates:
        file_path = (root / Path(*relative.parts)).resolve()
        try:
            file_path.relative_to(root)
        except ValueError:
            continue
        if file_path.is_file():
            try:
                return relative.as_posix(), file_path.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def help_title(markdown: str, fallback: str) -> str:
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


class HelpDialog(QDialog):
    def __init__(self, page: str | None = None, path: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        layout.addWidget(self.browser, 1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton(t("Close"))
        close.setProperty("variant", "secondary")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        self._current = ""
        self.load(page=page, path=path)

    def load(self, *, page: str | None = None, path: str | None = None) -> None:
        resolved = read_help(page=page, path=path)
        if resolved is None:
            self.setWindowTitle(t("Help"))
            self.browser.setPlainText(t("Help file not found."))
            return
        self._current, markdown = resolved
        self.setWindowTitle(help_title(markdown, t("Help")))
        self.browser.setMarkdown(markdown)

    def _open_link(self, url: QUrl) -> None:
        target = url.toString()
        if url.scheme() in {"http", "https", "mailto"}:
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(url)
            return
        if target.endswith(".md"):
            base = PurePosixPath(self._current).parent if self._current else PurePosixPath()
            joined = (base / target).as_posix()
            normalized = re.sub(r"(^|/)\./", r"\1", joined)
            parts: list[str] = []
            for part in normalized.split("/"):
                if part == "..":
                    if parts:
                        parts.pop()
                elif part:
                    parts.append(part)
            self.load(path="/".join(parts))


def show_help(parent: QWidget | None, page: str) -> None:
    dialog = HelpDialog(page=page, parent=parent)
    dialog.exec()
