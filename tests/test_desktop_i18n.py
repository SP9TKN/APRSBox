"""Polish coverage of the desktop window's texts (app/desktop/languages/pl.json)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app" / "desktop"
CATALOG = DESKTOP / "languages" / "pl.json"


def desktop_literals() -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for path in sorted(DESKTOP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            first = node.args[0]
            if name in {"t", "tf"} and isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.setdefault(first.value, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return keys


@pytest.fixture()
def polish():
    from app.desktop import i18n

    previous = i18n._language  # noqa: SLF001
    i18n.set_language("pl")
    yield i18n
    i18n._language = previous  # noqa: SLF001
    i18n._translator_for.cache_clear()  # noqa: SLF001


def test_every_desktop_text_has_a_polish_translation() -> None:
    from app.desktop.i18n import missing_desktop_keys

    keys = desktop_literals()
    assert len(keys) > 500
    missing = missing_desktop_keys("pl", sorted(keys))
    assert missing == [], "\n".join(f"{key!r} ({keys[key][0]})" for key in missing)


def test_catalog_keeps_placeholders() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    for key, value in catalog.items():
        if key.startswith("__"):
            continue
        assert sorted(re.findall(r"\{(\w+)\}", key)) == sorted(re.findall(r"\{(\w+)\}", value)), key


def test_dynamic_backend_messages_use_patterns(polish) -> None:
    assert polish.t("Object queued as job #12.") == "Obiekt dodany do kolejki jako zadanie #12."
    assert polish.t("WX queued as jobs #3, 4.") == "Ramka WX dodana do kolejki jako zadania #3, 4."
    assert polish.t("Something completely unknown.") == "Something completely unknown."


def test_web_catalog_still_translates_shared_texts(polish) -> None:
    assert polish.t("Messages") != "Messages"
    assert polish.tf("Visible levels: {levels}", levels="INFO") .endswith("INFO")
