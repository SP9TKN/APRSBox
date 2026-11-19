"""Alert sound playback (web: <audio loop> with aprs-audio-alert.mp3).

Windows plays MP3 through the MCI interface of winmm.dll, so no extra audio
dependency is needed.  On other platforms (tests in CI) playback is a no-op.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from app.config import settings

ALERT_SOUND = Path(settings.static_dir) / "audio" / "aprs-audio-alert.mp3"
_ALIAS = "aprsbox_alert"
_lock = threading.Lock()
_playing = False


def _mci(command: str) -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.winmm.mciSendStringW(command, None, 0, None))
    except Exception:
        return -1


def play_alert(loop: bool = True) -> bool:
    """Start the alert sound (looping until ``stop_alert``)."""
    global _playing
    if sys.platform != "win32" or not ALERT_SOUND.exists():
        return False
    with _lock:
        _mci(f"close {_ALIAS}")
        if _mci(f'open "{ALERT_SOUND}" type mpegvideo alias {_ALIAS}') != 0:
            return False
        result = _mci(f"play {_ALIAS} from 0{' repeat' if loop else ''}")
        _playing = result == 0
        return _playing


def stop_alert() -> None:
    global _playing
    with _lock:
        if sys.platform == "win32":
            _mci(f"stop {_ALIAS}")
            _mci(f"close {_ALIAS}")
        _playing = False


def is_playing() -> bool:
    return _playing
