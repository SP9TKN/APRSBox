"""Tests must not change the user's Windows autostart (HKCU Run)."""

from __future__ import annotations

import sys

import pytest


def _registry_value() -> str | None:
    import winreg

    from app.desktop import autostart

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0, winreg.KEY_READ) as key:
            return str(winreg.QueryValueEx(key, autostart.VALUE_NAME)[0])
    except OSError:
        return None


def test_the_autostart_of_a_test_session_is_a_stand_in() -> None:
    from app.desktop import autostart

    # First: if this fails the checks below would write to the real registry.
    assert autostart.set_enabled.__module__ == "conftest"
    assert autostart.is_enabled.__module__ == "conftest"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_switching_autostart_in_a_test_leaves_the_registry_alone() -> None:
    from app.desktop import autostart

    assert autostart.set_enabled.__module__ == "conftest"
    before = _registry_value()
    autostart.set_enabled(True)
    try:
        assert autostart.is_enabled()
        assert _registry_value() == before
    finally:
        autostart.set_enabled(False)
    assert not autostart.is_enabled()
    assert _registry_value() == before
