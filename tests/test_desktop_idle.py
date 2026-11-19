"""Is anybody at the computer (app.desktop.idle): the map refreshes slower while nobody is."""

from __future__ import annotations

import sys

import pytest

from app.desktop import idle


@pytest.mark.skipif(sys.platform != "win32", reason="GetLastInputInfo is Windows only")
def test_seconds_since_input_reads_the_session_input_clock() -> None:
    seconds = idle.seconds_since_input()
    assert seconds is not None and 0 <= seconds < 50 * 86400


def test_nobody_at_computer_after_ten_minutes_without_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(idle.SLOWDOWN_ENV, "1")
    monkeypatch.setattr(idle, "seconds_since_input", lambda: idle.IDLE_AFTER_SECONDS - 1.0)
    assert not idle.nobody_at_computer()
    monkeypatch.setattr(idle, "seconds_since_input", lambda: idle.IDLE_AFTER_SECONDS + 1.0)
    assert idle.nobody_at_computer()
    monkeypatch.setenv(idle.SLOWDOWN_ENV, "0")  # the tests and the screenshot tools
    assert not idle.nobody_at_computer()
    monkeypatch.setenv(idle.SLOWDOWN_ENV, "1")
    monkeypatch.setattr(idle, "seconds_since_input", lambda: None)  # unknown: never slow down
    assert not idle.nobody_at_computer()
