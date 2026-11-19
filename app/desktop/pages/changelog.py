"""Changelog page (web: templates/changelog.html)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QTextBrowser

from app.desktop.i18n import current_language, t
from app.desktop.widgets.common import Card, Page

REPO_ROOT = Path(__file__).resolve().parents[3]
CHANGELOG_FILES = {
    "pl": REPO_ROOT / "changelog.md",
    "en": REPO_ROOT / "changelog.en.md",
    "es": REPO_ROOT / "changelog.es.md",
    "de": REPO_ROOT / "changelog.de.md",
}
FALLBACK_ORDER = ("pl", "en")


def read_changelog(language: str) -> str:
    checked: list[Path] = []
    for code in (language, *FALLBACK_ORDER):
        path = CHANGELOG_FILES.get(code)
        if path is None or path in checked:
            continue
        checked.append(path)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return "# Changelog\n\nUnable to read changelog file."


class ChangelogPage(Page):
    page_key = "changelog"
    scrollable = False

    def build(self) -> None:
        card = Card(t("Changelog"))
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        card.add(self.browser, 1)
        self.body.addWidget(card, 1)
        self._language = ""

    def refresh(self) -> None:
        language = current_language()
        if language == self._language:
            return
        self._language = language
        markdown = read_changelog(language)
        if markdown.strip():
            self.browser.setMarkdown(markdown)
        else:
            self.browser.setPlainText(t("No records yet."))
