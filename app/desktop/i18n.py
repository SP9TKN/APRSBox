"""Translations for the desktop window.

Texts shared with the web GUI keep their original English source strings, so
the existing catalogs in ``app/languages`` translate them.  Texts that exist
only in the desktop window are translated by ``app/desktop/languages``.
"""

from __future__ import annotations

import atexit
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.i18n import DEFAULT_LANGUAGE, get_app_language, get_translator, normalize_language

DESKTOP_LANGUAGES_DIR = Path(__file__).resolve().parent / "languages"

_language: str | None = None

# Development aid: APRSBOX_I18N_MISSES_FILE=path records texts shown untranslated.
_MISSES_FILE = os.environ.get("APRSBOX_I18N_MISSES_FILE", "").strip()
_misses: set[str] = set()


def _write_misses() -> None:
    if _MISSES_FILE and _misses:
        try:
            existing = set(json.loads(Path(_MISSES_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            existing = set()
        Path(_MISSES_FILE).write_text(json.dumps(sorted(existing | _misses), ensure_ascii=False, indent=1), encoding="utf-8")


if _MISSES_FILE:
    atexit.register(_write_misses)


@lru_cache(maxsize=None)
def _desktop_catalog(language: str) -> dict[str, str]:
    path = DESKTOP_LANGUAGES_DIR / f"{language}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(value) for key, value in data.items() if not str(key).startswith("__")}


@lru_cache(maxsize=None)
def _desktop_patterns(language: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Regex translations for dynamic backend messages (catalog key "__patterns__")."""
    path = DESKTOP_LANGUAGES_DIR / f"{language}.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("__patterns__") or []
    except (OSError, ValueError, AttributeError):
        return ()
    compiled: list[tuple[re.Pattern[str], str]] = []
    for entry in entries:
        try:
            compiled.append((re.compile(str(entry[0])), str(entry[1])))
        except (re.error, IndexError, TypeError):
            continue
    return tuple(compiled)


def current_language() -> str:
    global _language
    if _language is None:
        _language = get_app_language()
    return _language


def set_language(code: str | None) -> None:
    global _language
    _language = normalize_language(code)
    _translator_for.cache_clear()
    _desktop_catalog.cache_clear()
    _desktop_patterns.cache_clear()


@lru_cache(maxsize=8)
def _translator_for(language: str):
    return get_translator(language)


def t(message: Any) -> str:
    text = str(message or "")
    if not text:
        return ""
    language = current_language()
    desktop = _desktop_catalog(language)
    if text in desktop:
        return desktop[text]
    translated = _translator_for(language)(text)
    if translated != text or language == DEFAULT_LANGUAGE:
        return translated
    for pattern, template in _desktop_patterns(language):
        if pattern.fullmatch(text):
            return pattern.sub(template, text)
    english = _desktop_catalog(DEFAULT_LANGUAGE)
    result = english.get(text, text)
    if _MISSES_FILE and result == text:
        _misses.add(text)
    return result


def tf(message: Any, **params: Any) -> str:
    template = t(message)
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, ValueError, IndexError):
        return template


def missing_desktop_keys(language: str, keys: list[str]) -> list[str]:
    """Keys that neither the desktop nor the web catalog translates (tests use it)."""
    desktop = _desktop_catalog(language)
    web = get_translator(language)
    return [key for key in keys if key not in desktop and web(key) == key]


def status_text(value: Any) -> str:
    """Runtime status words ("connected", "idle") -> catalog labels ("Connected", "Idle")."""
    text = str(value or "").strip()
    if not text:
        return ""
    translated = t(text)
    if translated != text:
        return translated
    label = text.replace("_", " ").capitalize()
    translated = t(label)
    return translated if translated != label else text
