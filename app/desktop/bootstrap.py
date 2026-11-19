"""Process-level setup that must run before ``app.config`` is imported."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]


def configure_environment(*, data_root: Path | None = None) -> Path:
    """Point the runtime directories at the application folder (portable layout).

    ``data/``, ``logs/``, ``config/`` and ``backups/`` live next to ``app/`` unless
    ``APRSBOX_HOME`` or ``data_root`` says otherwise.  Explicit ``APRSBOX_*``
    variables still win, which the tests rely on.
    """
    root = Path(data_root or os.environ.get("APRSBOX_HOME") or APP_ROOT).resolve()
    os.environ.setdefault("APRSBOX_ENV", "development")
    os.environ.setdefault("APRSBOX_DB_PATH", str(root / "data" / "aprsbox.db"))
    os.environ.setdefault("APRSBOX_LOG_DIR", str(root / "logs"))
    os.environ.setdefault("APRSBOX_CONFIG_DIR", str(root / "config"))
    os.environ.setdefault("APRSBOX_BACKUPS_DIR", str(root / "backups"))
    os.environ.setdefault("APRSBOX_INSTALL_ROOT", str(root))
    if sys.platform == "win32":
        _set_app_user_model_id()
    return root


def _set_app_user_model_id() -> None:
    # Gives the window its own taskbar button/icon instead of python.exe's.
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SQ9MDD.APRSBox.Desktop")
    except Exception:
        pass


def windows_ui_language() -> str | None:
    """Two-letter code of the Windows display language (None elsewhere)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import locale

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        name = locale.windows_locale.get(int(lang_id), "")
        return name.split("_", 1)[0].lower() or None
    except Exception:
        return None
