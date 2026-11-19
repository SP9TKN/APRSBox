from __future__ import annotations

import array
import os
import select
import sys
import time

IS_WINDOWS = sys.platform == "win32"
SERIAL_BAUD_RATE_CHOICES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

if IS_WINDOWS:
    from app.services import serial_tnc_windows as _windows_serial

    fcntl = None
    termios = None
    SUPPORTED_SERIAL_BAUD_RATES: dict[int, int] = {value: value for value in SERIAL_BAUD_RATE_CHOICES}
else:
    import fcntl
    import termios

    _windows_serial = None
    SUPPORTED_SERIAL_BAUD_RATES = {
        value: getattr(termios, f"B{value}")
        for value in SERIAL_BAUD_RATE_CHOICES
        if hasattr(termios, f"B{value}")
    }


def normalize_serial_device_path(value: object, *, label: str = "Serial device path") -> str:
    path = str(value or "").strip()
    if not path:
        raise ValueError(f"{label} is required for serial TNC.")
    return path


def normalize_serial_baud_rate(value: object, *, label: str = "Baud rate") -> int:
    try:
        baud_rate = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be one of: {', '.join(str(item) for item in sorted(SUPPORTED_SERIAL_BAUD_RATES))}.") from exc
    if baud_rate not in SUPPORTED_SERIAL_BAUD_RATES:
        raise ValueError(f"{label} must be one of: {', '.join(str(item) for item in sorted(SUPPORTED_SERIAL_BAUD_RATES))}.")
    return baud_rate


def open_serial_device(path: str, baud_rate: int, *, flush_buffers: bool = True) -> int:
    normalized_path = normalize_serial_device_path(path)
    normalized_baud_rate = normalize_serial_baud_rate(baud_rate)
    if IS_WINDOWS:
        return _windows_serial.open_device(normalized_path, normalized_baud_rate, flush_buffers=flush_buffers)
    open_flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | int(getattr(os, "O_CLOEXEC", 0))
    fd = os.open(normalized_path, open_flags)
    try:
        attributes = termios.tcgetattr(fd)
        attributes[0] = 0
        attributes[1] = 0
        cflag = termios.CLOCAL | termios.CREAD
        cflag &= ~int(getattr(termios, "PARENB", 0))
        cflag &= ~int(getattr(termios, "CSTOPB", 0))
        cflag &= ~int(getattr(termios, "CSIZE", 0))
        if hasattr(termios, "CRTSCTS"):
            cflag &= ~int(termios.CRTSCTS)
        cflag |= termios.CS8
        attributes[2] = cflag
        attributes[3] = 0
        attributes[6][termios.VMIN] = 1
        attributes[6][termios.VTIME] = 0
        speed = SUPPORTED_SERIAL_BAUD_RATES[normalized_baud_rate]
        attributes[4] = speed
        attributes[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attributes)
        if flush_buffers:
            termios.tcflush(fd, termios.TCIOFLUSH)
    except Exception:
        os.close(fd)
        raise
    return fd


def close_serial_device(
    fd: int | None,
    *,
    drain_timeout: float = 0.2,
    flush_buffers: bool = True,
    drop_control_lines: bool = False,
) -> None:
    if fd is None:
        return
    if IS_WINDOWS:
        _windows_serial.close_device(
            fd,
            drain_timeout=drain_timeout,
            flush_buffers=flush_buffers,
            drop_control_lines=drop_control_lines,
        )
        return
    if drain_timeout > 0:
        try:
            _best_effort_drain(fd, timeout=drain_timeout)
        except OSError:
            pass
    if flush_buffers:
        try:
            termios.tcflush(fd, termios.TCIOFLUSH)
        except OSError:
            pass
    if drop_control_lines:
        _best_effort_set_modem_lines(fd, dtr=False, rts=False)
    try:
        os.close(fd)
    except OSError:
        pass


def read_serial_chunk(fd: int, *, max_bytes: int = 1024, timeout: float = 1.0) -> bytes:
    if IS_WINDOWS:
        return _windows_serial.read_chunk(fd, max_bytes=max_bytes, timeout=timeout)
    try:
        readable, _, _ = select.select([fd], [], [], timeout)
    except InterruptedError:
        return b""
    if not readable:
        return b""
    try:
        return os.read(fd, max_bytes)
    except BlockingIOError:
        return b""


def write_serial_data(fd: int, data: bytes, *, timeout: float = 1.0, drain: bool = False) -> None:
    if IS_WINDOWS:
        _windows_serial.write_data(fd, data, timeout=timeout, drain=drain)
        return
    deadline = time.monotonic() + timeout
    offset = 0
    while offset < len(data):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Serial write timed out.")
        try:
            _, writable, _ = select.select([], [fd], [], remaining)
        except InterruptedError:
            continue
        if not writable:
            raise TimeoutError("Serial write timed out.")
        try:
            written = os.write(fd, data[offset:])
        except BlockingIOError:
            continue
        if written <= 0:
            raise OSError("Serial write failed.")
        offset += written
    if drain:
        _best_effort_drain(fd, timeout=timeout)


def _best_effort_drain(fd: int, *, timeout: float) -> None:
    # Prefer a bounded drain strategy to avoid indefinite blocking on pseudo terminals
    # and edge-case drivers.
    if IS_WINDOWS:
        _windows_serial.drain(fd, timeout=timeout)
        return
    tiocoutq = getattr(termios, "TIOCOUTQ", None)
    if tiocoutq is None:
        return

    deadline = time.monotonic() + max(0.05, float(timeout))
    pending = array.array("i", [0])
    while time.monotonic() < deadline:
        try:
            fcntl.ioctl(fd, tiocoutq, pending, True)
        except OSError:
            return
        if pending[0] <= 0:
            return
        time.sleep(0.01)


def _best_effort_set_modem_lines(fd: int, *, dtr: bool | None, rts: bool | None) -> None:
    if IS_WINDOWS:
        _windows_serial.set_modem_lines(fd, dtr=dtr, rts=rts)
        return
    tiocmbic = getattr(termios, "TIOCMBIC", None)
    tiocmbis = getattr(termios, "TIOCMBIS", None)
    dtr_mask = getattr(termios, "TIOCM_DTR", None)
    rts_mask = getattr(termios, "TIOCM_RTS", None)
    if tiocmbic is None or tiocmbis is None:
        return

    clear_mask = 0
    set_mask = 0
    if dtr is not None and dtr_mask is not None:
        if dtr:
            set_mask |= int(dtr_mask)
        else:
            clear_mask |= int(dtr_mask)
    if rts is not None and rts_mask is not None:
        if rts:
            set_mask |= int(rts_mask)
        else:
            clear_mask |= int(rts_mask)

    if clear_mask:
        try:
            clear_bits = array.array("i", [clear_mask])
            fcntl.ioctl(fd, tiocmbic, clear_bits, True)
        except OSError:
            pass
    if set_mask:
        try:
            set_bits = array.array("i", [set_mask])
            fcntl.ioctl(fd, tiocmbis, set_bits, True)
        except OSError:
            pass


def list_serial_ports() -> list[dict[str, str]]:
    """Serial ports for interface forms; empty on POSIX (paths are typed by hand)."""
    if IS_WINDOWS:
        return _windows_serial.list_serial_ports()
    return []
