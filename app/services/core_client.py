from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings

# The desktop build hosts the core runtime in-process; when set, calls go to it
# directly instead of the aprsbox-core HTTP API.
_LOCAL_CORE: Any = None


def set_local_core(runtime: Any) -> None:
    global _LOCAL_CORE
    _LOCAL_CORE = runtime


def unavailable_traffic_snapshot(detail: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "status_detail": detail,
        "active_modem": None,
        "expose": {
            "enabled": False,
            "bind_address": None,
            "port": None,
            "active_clients": 0,
            "listen_endpoint": None,
        },
        "interfaces": [],
        "last_error": detail,
        "updated_at": None,
        "frames": [],
    }


def get_core_traffic_snapshot() -> dict[str, Any]:
    if _LOCAL_CORE is not None:
        try:
            snapshot = _LOCAL_CORE.traffic_runtime_snapshot()
        except Exception as exc:
            return unavailable_traffic_snapshot(f"aprs-core unavailable: {exc}")
        return snapshot or unavailable_traffic_snapshot("aprs-core is not running.")
    try:
        with urlopen(f"{settings.core_base_url}/api/traffic", timeout=1.5) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        return unavailable_traffic_snapshot(f"aprs-core HTTP error: {exc.code}")
    except URLError as exc:
        return unavailable_traffic_snapshot(f"aprs-core unavailable: {exc.reason}")
    except OSError as exc:
        return unavailable_traffic_snapshot(f"aprs-core connection failed: {exc}")

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return unavailable_traffic_snapshot("aprs-core returned invalid JSON.")


def restart_core_traffic_monitor() -> dict[str, Any]:
    if _LOCAL_CORE is not None:
        return _LOCAL_CORE.restart_traffic_monitor()
    request = Request(f"{settings.core_base_url}/api/traffic/restart", method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        return {"ok": False, "error": f"aprs-core HTTP error: {exc.code}"}
    except URLError as exc:
        return {"ok": False, "error": f"aprs-core unavailable: {exc.reason}"}
    except OSError as exc:
        return {"ok": False, "error": f"aprs-core connection failed: {exc}"}

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "error": "aprs-core returned invalid JSON."}
    if not bool(parsed.get("ok")):
        return {"ok": False, "error": str(parsed.get("error") or "aprs-core restart failed")}
    return {"ok": True}
