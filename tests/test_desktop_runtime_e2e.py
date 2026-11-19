"""End-to-end test of the in-process APRS runtime, fully offline.

A fake KISS TCP TNC and a fake APRS-IS server listen on random 127.0.0.1
ports.  The real runtime (the same one the desktop window starts) connects to
them, receives frames from both and transmits a beacon to the fake TNC.
Name resolution is restricted to localhost for the whole test, so nothing can
reach a real APRS-IS server, GitHub or any other host.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

import desktop_support

ENV_KEYS = ("APRSBOX_DB_PATH", "APRSBOX_LOG_DIR", "APRSBOX_CONFIG_DIR", "APRSBOX_BACKUPS_DIR")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", None, ""}
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


class FakeServer:
    """One-client TCP server on 127.0.0.1 that records what the client sends."""

    def __init__(self, greeting: bytes = b"") -> None:
        self.greeting = greeting
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.client: socket.socket | None = None
        self.received = bytearray()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stopping.is_set():
            if self.client is None:
                try:
                    client, _address = self.listener.accept()
                except (TimeoutError, OSError):
                    continue
                client.settimeout(0.2)
                self.client = client
                if self.greeting:
                    client.sendall(self.greeting)
                continue
            try:
                chunk = self.client.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                self.client = None
                continue
            if not chunk:
                self.client = None
                continue
            with self.lock:
                self.received.extend(chunk)

    def send(self, data: bytes) -> None:
        assert self.client is not None, "client not connected"
        self.client.sendall(data)

    def data(self) -> bytes:
        with self.lock:
            return bytes(self.received)

    def close(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=3)
        for sock in (self.client, self.listener):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def kiss_payloads(data: bytes) -> list[bytes]:
    frames: list[bytes] = []
    for chunk in data.split(bytes([FEND])):
        if not chunk:
            continue
        unescaped = bytearray()
        escape = False
        for byte in chunk:
            if escape:
                unescaped.append(FEND if byte == TFEND else FESC if byte == TFESC else byte)
                escape = False
            elif byte == FESC:
                escape = True
            else:
                unescaped.append(byte)
        if unescaped and unescaped[0] & 0x0F == 0x00:
            frames.append(bytes(unescaped[1:]))
    return frames


def wait_for(predicate: Callable[[], Any], timeout: float = 20.0, interval: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return predicate()


@pytest.fixture()
def offline_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host: Any, *args: Any, **kwargs: Any):
        name = host.decode() if isinstance(host, bytes) else host
        if name not in LOCAL_HOSTS:
            raise socket.gaierror(f"offline test: {name} is not allowed")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture()
def isolated_db(tmp_path: Path):
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    desktop_support.isolated_environment(tmp_path)
    try:
        yield tmp_path
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_runtime_receives_from_kiss_and_aprsis_and_transmits_beacon(offline_network: None, isolated_db: Path) -> None:
    from app.db import execute, fetch_one, init_db, set_app_setting
    from app.desktop.runtime import CoreRuntime, set_active_runtime
    from app.services.content import get_station_settings
    from app.services.outbound import build_tnc2_kiss_frame, enqueue_beacon_job
    from app.services.traffic import decode_ax25_to_tnc2

    tnc = FakeServer()
    aprsis = FakeServer(greeting=b"# aprsc 2.1.14-test\r\n")
    runtime = CoreRuntime()
    try:
        init_db()
        interface_id = desktop_support.seed_tcp_interface(name="Fake TNC", host_port=f"127.0.0.1:{tnc.port}", enabled=True)
        desktop_support.seed_station_settings(interface_id=interface_id, tx_enabled=True)
        execute(
            "INSERT INTO modems(name, modem_type, band, device_path, baud_rate, enabled, notes, created_at, updated_at) "
            "VALUES ('APRS-IS', 'APRSIS', '', 'r/50.29/18.67/50', NULL, 1, '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        set_app_setting("aprsis_server", "127.0.0.1")
        set_app_setting("aprsis_port", str(aprsis.port))
        assert tnc.port != 8100 and aprsis.port != 8100

        set_active_runtime(runtime)
        runtime.start(timeout=30)
        assert runtime.running

        # 1. RF receive through the KISS TCP TNC.
        assert wait_for(lambda: tnc.client is not None), "runtime did not connect to the fake TNC"
        tnc.send(build_tnc2_kiss_frame("SP9ABC>APRS,WIDE1-1:!5018.72N/01844.10E-E2E rf test"))
        assert wait_for(lambda: fetch_one("SELECT id FROM traffic_frames WHERE line LIKE '%E2E rf test%'")), \
            "RF frame was not stored"

        # 2. APRS-IS login and receive.
        login = wait_for(lambda: b"\n" in aprsis.data() and aprsis.data())
        assert login, "runtime did not log in to the fake APRS-IS server"
        login_line = login.decode("ascii", "replace").splitlines()[0]
        assert login_line.startswith("user SQ9TST pass ") and "filter" in login_line
        aprsis.send(b"# logresp SQ9TST verified, server T2TEST\r\n")
        aprsis.send(b"SP9XYZ>APRS,TCPIP*,qAC,T2TEST:>E2E aprsis test\r\n")
        assert wait_for(lambda: fetch_one("SELECT id FROM traffic_frames WHERE line LIKE '%E2E aprsis test%'")), \
            "APRS-IS line was not stored"

        # 3. Beacon transmission reaches the TNC as a KISS data frame.
        before = len(kiss_payloads(tnc.data()))
        success, message = enqueue_beacon_job(get_station_settings())
        assert success, message

        def beacon_line() -> str | None:
            for payload in kiss_payloads(tnc.data())[before:]:
                line = decode_ax25_to_tnc2(payload)
                # The decoder formats the header as "SRC > DST , PATH".
                line = (line or "").replace(" > ", ">").replace(" , ", ",")
                if line.startswith("SQ9TST>"):
                    return line
            return None

        line = wait_for(beacon_line, timeout=30)
        if not line:
            from app.db import fetch_all

            jobs = [dict(row) for row in fetch_all("SELECT id, kind, status, attempt_count, last_error, interface_id FROM outbound_jobs")]
            logs = [f"{row['level']} [{row['category']}] {row['message']}" for row in fetch_all(
                "SELECT level, category, message FROM event_logs ORDER BY id")]
            details = "\n".join(logs[-60:])
            pytest.fail(f"beacon was not transmitted to the fake TNC\njobs: {jobs}\nlogs:\n{details}")
        assert "WIDE1-1" in line and "APRSBox desktop test" in line
    finally:
        runtime.stop(timeout=20)
        set_active_runtime(None)
        tnc.close()
        aprsis.close()
