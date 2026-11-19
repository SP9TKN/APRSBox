"""Cross-platform stand-in for a serial device pair used by the serial TNC tests.

On POSIX a pty pair is used, exactly as before.  Windows has no pty, so the
"device" handed to the application is the pyserial URL ``socket://127.0.0.1:N``
and the test keeps the listening end ("master").  The application opening the
serial device is then a TCP connect to that listener.
"""

from __future__ import annotations

import contextlib
import os
import select
import socket
import sys
import time
from typing import Iterator

IS_WINDOWS = sys.platform == "win32"


class SocketSerialMaster:
    """Test-side end of a ``socket://`` serial device.

    The application may close and reopen the device (reconnect after an error),
    so the newest accepted connection always wins.
    """

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._conn: socket.socket | None = None
        self.port = int(self._listener.getsockname()[1])

    @property
    def device_path(self) -> str:
        return f"socket://127.0.0.1:{self.port}"

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(OSError):
                self._conn.close()
            self._conn = None
        with contextlib.suppress(OSError):
            self._listener.close()

    def _accept_pending(self, timeout: float) -> None:
        readable, _, _ = select.select([self._listener], [], [], max(0.0, timeout))
        while readable:
            conn, _ = self._listener.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._conn is not None:
                with contextlib.suppress(OSError):
                    self._conn.close()
            self._conn = conn
            readable, _, _ = select.select([self._listener], [], [], 0)

    def _ensure_connection(self, deadline: float) -> socket.socket | None:
        self._accept_pending(0)
        while self._conn is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._accept_pending(remaining)
        return self._conn

    def write(self, data: bytes, *, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        conn = self._ensure_connection(deadline)
        if conn is None:
            raise TimeoutError("Application did not open the socket serial device.")
        conn.sendall(data)

    def read_chunk(self, *, timeout: float = 1.0) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            conn = self._ensure_connection(deadline)
            if conn is None:
                return b""
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([conn, self._listener], [], [], remaining)
            if not readable:
                return b""
            if self._listener in readable:
                self._accept_pending(0)
                continue
            try:
                chunk = conn.recv(1024)
            except OSError:
                chunk = b""
            if chunk:
                return chunk
            # Peer closed this connection; wait for the application to reopen.
            with contextlib.suppress(OSError):
                conn.close()
            if self._conn is conn:
                self._conn = None
            if time.monotonic() >= deadline:
                return b""


@contextlib.contextmanager
def pseudo_serial_device() -> Iterator[tuple[object, str]]:
    if IS_WINDOWS:
        master = SocketSerialMaster()
        try:
            yield master, master.device_path
        finally:
            master.close()
        return
    import pty

    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    try:
        yield master_fd, slave_path
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def master_write(master: object, data: bytes) -> None:
    if isinstance(master, SocketSerialMaster):
        master.write(data)
        return
    os.write(int(master), data)  # type: ignore[arg-type]


def read_master_chunk(master: object, *, timeout: float = 1.0) -> bytes:
    if isinstance(master, SocketSerialMaster):
        return master.read_chunk(timeout=timeout)
    readable, _, _ = select.select([master], [], [], timeout)
    if not readable:
        return b""
    return os.read(int(master), 1024)  # type: ignore[arg-type]
