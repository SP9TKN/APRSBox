import sys
import threading
import unittest

from app.services import serial_tnc
from serial_pair import SocketSerialMaster


@unittest.skipUnless(sys.platform == "win32", "Windows pyserial backend")
class SerialTncWindowsBackendTests(unittest.TestCase):
    def test_supported_baud_rates_match_posix_choices(self) -> None:
        self.assertEqual(
            sorted(serial_tnc.SUPPORTED_SERIAL_BAUD_RATES),
            [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200],
        )

    def test_loopback_device_round_trips_kiss_bytes(self) -> None:
        handle = serial_tnc.open_serial_device("loop://", 9600)
        try:
            self.assertIsInstance(handle, int)
            payload = bytes([0xC0, 0x00]) + b"SQ9MDD>APRS:>loop" + bytes([0xC0])
            serial_tnc.write_serial_data(handle, payload, drain=True)
            received = b""
            while len(received) < len(payload):
                chunk = serial_tnc.read_serial_chunk(handle, timeout=1.0)
                self.assertTrue(chunk, "loopback returned no data")
                received += chunk
            self.assertEqual(received, payload)
        finally:
            serial_tnc.close_serial_device(handle)

    def test_read_returns_empty_bytes_after_timeout(self) -> None:
        handle = serial_tnc.open_serial_device("loop://", 9600)
        try:
            self.assertEqual(serial_tnc.read_serial_chunk(handle, timeout=0.05), b"")
        finally:
            serial_tnc.close_serial_device(handle)

    def test_read_respects_max_bytes(self) -> None:
        handle = serial_tnc.open_serial_device("loop://", 9600)
        try:
            serial_tnc.write_serial_data(handle, b"0123456789")
            first = serial_tnc.read_serial_chunk(handle, max_bytes=4, timeout=1.0)
            self.assertEqual(first, b"0123")
            rest = serial_tnc.read_serial_chunk(handle, max_bytes=64, timeout=1.0)
            self.assertEqual(rest, b"456789")
        finally:
            serial_tnc.close_serial_device(handle)

    def test_socket_device_exchanges_data_both_ways(self) -> None:
        master = SocketSerialMaster()
        try:
            handle = serial_tnc.open_serial_device(master.device_path, 9600)
            try:
                master.write(b"from-tnc")
                received = b""
                while len(received) < len(b"from-tnc"):
                    chunk = serial_tnc.read_serial_chunk(handle, timeout=1.0)
                    self.assertTrue(chunk)
                    received += chunk
                self.assertEqual(received, b"from-tnc")
                serial_tnc.write_serial_data(handle, b"to-tnc", drain=True)
                self.assertEqual(master.read_chunk(timeout=1.0), b"to-tnc")
            finally:
                serial_tnc.close_serial_device(handle, drop_control_lines=True)
        finally:
            master.close()

    def test_open_missing_port_raises_oserror(self) -> None:
        with self.assertRaises(OSError):
            serial_tnc.open_serial_device("COM250", 9600)

    def test_close_unknown_handle_is_noop_and_closed_handle_is_rejected(self) -> None:
        serial_tnc.close_serial_device(None)
        serial_tnc.close_serial_device(987654)
        handle = serial_tnc.open_serial_device("loop://", 9600)
        serial_tnc.close_serial_device(handle)
        with self.assertRaises(OSError):
            serial_tnc.read_serial_chunk(handle, timeout=0.01)
        with self.assertRaises(OSError):
            serial_tnc.write_serial_data(handle, b"x")

    def test_concurrent_read_and_write_on_one_handle(self) -> None:
        handle = serial_tnc.open_serial_device("loop://", 9600)
        received = bytearray()
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set() and len(received) < 2000:
                received.extend(serial_tnc.read_serial_chunk(handle, timeout=0.2))

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for index in range(200):
                serial_tnc.write_serial_data(handle, f"{index:09d}\n".encode())
            thread.join(timeout=5.0)
        finally:
            stop.set()
            thread.join(timeout=2.0)
            serial_tnc.close_serial_device(handle)
        expected = b"".join(f"{index:09d}\n".encode() for index in range(200))
        self.assertEqual(bytes(received), expected)

    def test_list_serial_ports_returns_device_dicts(self) -> None:
        ports = serial_tnc.list_serial_ports()
        self.assertIsInstance(ports, list)
        for port in ports:
            self.assertIn("device", port)
            self.assertIn("description", port)


if __name__ == "__main__":
    unittest.main()
