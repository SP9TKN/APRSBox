"""Start APRSBox with Windows (per-user ``Run`` registry value, no admin rights needed)."""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "APRSBox"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PROJECT_ROOT / "APRSBox.pyw"


def is_supported() -> bool:
    return sys.platform == "win32"


def pythonw_executable() -> Path:
    """pythonw.exe next to the running interpreter (no console window)."""
    executable = Path(sys.executable)
    candidate = executable.with_name("pythonw.exe")
    return candidate if candidate.exists() else executable


def autostart_command() -> str:
    return f'"{pythonw_executable()}" "{LAUNCHER}" --minimized'


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return bool(str(value or "").strip())


def set_enabled(enabled: bool) -> None:
    if not is_supported():
        raise OSError("Autostart is available only on Windows.")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
