"""Windows serial backend for the KISS serial TNC runtime.

The POSIX implementation in ``serial_tnc`` works on raw file descriptors
(termios + select).  Windows has neither, so this module wraps pyserial and
hands out integer handles, which keeps the ``serial_tnc`` API (and every caller
that logs or stores the "fd") unchanged.

Besides real ports (``COM3``, ``\\\\.\\COM12``) every pyserial URL is accepted,
e.g. ``loop://`` (loopback) or ``socket://127.0.0.1:8001`` (raw TCP serial
bridge), which is what the offline tests use.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover - import guard exercised only without pyserial
    import serial  # type: ignore[import-untyped]
    import serial.tools.list_ports  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]


@dataclass(slots=True)
class _SerialHandle:
    port: Any
    path: str
    read_timeout: float | None = None
    write_timeout: float | None = None
    config_lock: threading.Lock = field(default_factory=threading.Lock)


_HANDLES: dict[int, _SerialHandle] = {}
_HANDLES_LOCK = threading.Lock()
_HANDLE_IDS = itertools.count(1)


def _require_pyserial() -> None:
    if serial is None:
        raise OSError("Serial TNC support on Windows requires the pyserial package.")


def _lookup(handle: int) -> _SerialHandle:
    with _HANDLES_LOCK:
        entry = _HANDLES.get(int(handle))
    if entry is None:
        raise OSError(f"Serial handle {handle} is not open.")
    return entry


def open_device(path: str, baud_rate: int, *, flush_buffers: bool = True) -> int:
    _require_pyserial()
    try:
        port = serial.serial_for_url(path, do_not_open=True)
        port.baudrate = int(baud_rate)
        port.bytesize = serial.EIGHTBITS
        port.parity = serial.PARITY_NONE
        port.stopbits = serial.STOPBITS_ONE
        port.xonxoff = False
        port.rtscts = False
        port.dsrdtr = False
        port.timeout = 1.0
        port.write_timeout = 1.0
        port.open()
    except serial.SerialException as exc:
        raise OSError(str(exc)) from exc
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    entry = _SerialHandle(port=port, path=path, read_timeout=1.0, write_timeout=1.0)
    if flush_buffers:
        _reset_buffers(port)
    with _HANDLES_LOCK:
        handle = next(_HANDLE_IDS)
        _HANDLES[handle] = entry
    return handle


def close_device(
    handle: int | None,
    *,
    drain_timeout: float = 0.2,
    flush_buffers: bool = True,
    drop_control_lines: bool = False,
) -> None:
    if handle is None:
        return
    with _HANDLES_LOCK:
        entry = _HANDLES.pop(int(handle), None)
    if entry is None:
        return
    if drain_timeout > 0:
        _drain_port(entry.port, timeout=drain_timeout)
    if flush_buffers:
        _reset_buffers(entry.port)
    if drop_control_lines:
        _set_port_modem_lines(entry.port, dtr=False, rts=False)
    try:
        entry.port.close()
    except Exception:
        pass


def read_chunk(handle: int, *, max_bytes: int = 1024, timeout: float = 1.0) -> bytes:
    entry = _lookup(handle)
    port = entry.port
    timeout = max(0.0, float(timeout))
    try:
        with entry.config_lock:
            if entry.read_timeout != timeout:
                port.timeout = timeout
                entry.read_timeout = timeout
        first = port.read(1)
        if not first or max_bytes <= 1:
            return first
        if _is_native_port(port):
            # Native COM port: in_waiting is the exact number of buffered bytes.
            pending = int(port.in_waiting or 0)
            if pending > 0:
                return first + port.read(min(pending, max_bytes - 1))
            return first
        # URL handlers (socket://, loop://) report in_waiting as a flag only,
        # so drain whatever is already buffered with a non-blocking read.
        with entry.config_lock:
            port.timeout = 0
            try:
                rest = port.read(max_bytes - 1)
            finally:
                port.timeout = timeout
        return first + rest
    except serial.SerialException as exc:
        raise OSError(str(exc)) from exc


def write_data(handle: int, data: bytes, *, timeout: float = 1.0, drain: bool = False) -> None:
    entry = _lookup(handle)
    port = entry.port
    timeout = max(0.05, float(timeout))
    try:
        with entry.config_lock:
            if entry.write_timeout != timeout:
                port.write_timeout = timeout
                entry.write_timeout = timeout
        written = port.write(bytes(data))
    except serial.SerialTimeoutException as exc:
        raise TimeoutError("Serial write timed out.") from exc
    except serial.SerialException as exc:
        raise OSError(str(exc)) from exc
    if written is not None and int(written) < len(data):
        raise TimeoutError("Serial write timed out.")
    if drain:
        _drain_port(port, timeout=timeout)


def drain(handle: int, *, timeout: float) -> None:
    try:
        entry = _lookup(handle)
    except OSError:
        return
    _drain_port(entry.port, timeout=timeout)


def set_modem_lines(handle: int, *, dtr: bool | None, rts: bool | None) -> None:
    try:
        entry = _lookup(handle)
    except OSError:
        return
    _set_port_modem_lines(entry.port, dtr=dtr, rts=rts)


def list_serial_ports() -> list[dict[str, str]]:
    """Return available COM ports as ``[{"device": "COM3", "description": "..."}]``."""
    if serial is None:
        return []
    ports: list[dict[str, str]] = []
    try:
        for info in serial.tools.list_ports.comports():
            ports.append(
                {
                    "device": str(info.device or ""),
                    "description": str(info.description or ""),
                    "hwid": str(info.hwid or ""),
                }
            )
    except Exception:
        return []
    ports.sort(key=lambda item: _port_sort_key(item["device"]))
    return ports


def open_handle_count() -> int:
    with _HANDLES_LOCK:
        return len(_HANDLES)


def _is_native_port(port: Any) -> bool:
    return type(port).__module__ in {"serial.serialwin32", "serial.serialposix"}


def _port_sort_key(device: str) -> tuple[int, str]:
    text = str(device or "").upper().removeprefix("\\\\.\\")
    if text.startswith("COM") and text[3:].isdigit():
        return (int(text[3:]), text)
    return (1 << 30, text)


def _reset_buffers(port: Any) -> None:
    for method_name in ("reset_input_buffer", "reset_output_buffer"):
        method = getattr(port, method_name, None)
        if method is None:
            continue
        try:
            method()
        except Exception:
            pass


def _drain_port(port: Any, *, timeout: float) -> None:
    # Bounded drain: pyserial's flush() on Windows waits without a deadline.
    deadline = time.monotonic() + max(0.05, float(timeout))
    while time.monotonic() < deadline:
        try:
            pending = int(getattr(port, "out_waiting", 0) or 0)
        except Exception:
            return
        if pending <= 0:
            return
        time.sleep(0.01)


def _set_port_modem_lines(port: Any, *, dtr: bool | None, rts: bool | None) -> None:
    if dtr is not None:
        try:
            port.dtr = bool(dtr)
        except Exception:
            pass
    if rts is not None:
        try:
            port.rts = bool(rts)
        except Exception:
            pass
