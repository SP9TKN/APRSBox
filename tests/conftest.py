"""Shared pytest setup.

* Every test session gets its own initialized SQLite database, so tests that do
  not create a temporary database never touch (or depend on) ``data/aprsbox.db``
  of a developer checkout.
* Windows cannot delete files that are still open.  Several tests open SQLite
  connections without closing them (CPython frees sqlite3 connections only via
  the cyclic GC), so temporary directories collect garbage before cleanup.
* Service functions whose names start with ``test_`` (e.g.
  ``app.services.wx.test_wx_source_connection``) are not tests.
* Windows autostart is a stand-in kept in memory: the real one writes to the
  user's registry, and clicking every button in Settings changed it.
"""

from __future__ import annotations

import gc
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="aprsbox-tests-"))
for _name, _sub in (
    ("APRSBOX_DB_PATH", "data/aprsbox.db"),
    ("APRSBOX_LOG_DIR", "logs"),
    ("APRSBOX_CONFIG_DIR", "config"),
    ("APRSBOX_BACKUPS_DIR", "backups"),
):
    os.environ[_name] = str(_SESSION_ROOT / _sub)

_DEFERRED_CLEANUP: list[str] = []

if sys.platform == "win32":
    _original_cleanup = tempfile.TemporaryDirectory.cleanup

    def _cleanup_after_gc(self: tempfile.TemporaryDirectory) -> None:  # type: ignore[type-arg]
        gc.collect()
        try:
            _original_cleanup(self)
        except PermissionError:
            gc.collect()
            time.sleep(0.1)
            try:
                _original_cleanup(self)
            except PermissionError:
                # The test itself still holds the connection (local variable);
                # retry once the session is over instead of failing the test.
                _DEFERRED_CLEANUP.append(self.name)

    tempfile.TemporaryDirectory.cleanup = _cleanup_after_gc  # type: ignore[method-assign]


def _keep_windows_autostart_in_memory() -> None:
    from app.desktop import autostart

    enabled = {"value": False}

    def is_enabled() -> bool:
        return enabled["value"]

    def set_enabled(value: bool) -> None:
        enabled["value"] = bool(value)

    autostart.is_enabled = is_enabled
    autostart.set_enabled = set_enabled


_keep_windows_autostart_in_memory()


def pytest_sessionstart(session: pytest.Session) -> None:
    from app.db import init_db

    init_db()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    gc.collect()
    for path in _DEFERRED_CLEANUP:
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    kept: list[pytest.Item] = []
    for item in items:
        function = getattr(item, "function", None)
        module_name = str(getattr(function, "__module__", "") or "")
        if module_name.startswith("app."):
            continue
        kept.append(item)
    items[:] = kept
