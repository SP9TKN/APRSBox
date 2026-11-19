"""Network facts shown on the dashboard (Windows replacement of network_diagnostics).

The Linux dashboard showed how to reach the web panel (mDNS, avahi, web URL).
The desktop has no web panel; what LAN clients need instead are the address of
this PC and the shared KISS TCP ports of the interfaces.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

_ROUTE_PROBE = "192.0.2.1"  # TEST-NET-1: never contacted, only used to pick the outgoing route


def _usable(value: Any, version: int) -> str | None:
    candidate = str(value or "").strip().split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if address.version != version or address.is_loopback or address.is_unspecified:
        return None
    return str(address)


def primary_ipv4() -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.settimeout(0.3)
        probe.connect((_ROUTE_PROBE, 9))  # UDP connect sends no packet
        address = _usable(probe.getsockname()[0], 4)
        if address:
            return address
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = _usable(info[4][0], 4)
            if address:
                return address
    except OSError:
        pass
    return None


def primary_ipv6() -> str | None:
    candidates: list[tuple[int, str]] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
            address = _usable(info[4][0], 6)
            if address:
                link_local = ipaddress.ip_address(address).is_link_local
                candidates.append((1 if link_local else 0, address))
    except OSError:
        return None
    return min(candidates)[1] if candidates else None


def shared_kiss_ports() -> list[dict[str, Any]]:
    from app.db import fetch_all

    rows = fetch_all(
        """
        SELECT name, enabled, expose_port_enabled, expose_bind_address, expose_port, expose_allow_tx
        FROM modems
        WHERE expose_port_enabled = 1
        ORDER BY id
        """
    )
    return [
        {
            "name": str(row["name"] or ""),
            "enabled": bool(row["enabled"]),
            "endpoint": f"{row['expose_bind_address'] or '0.0.0.0'}:{row['expose_port'] or ''}",
            "allow_tx": bool(row["expose_allow_tx"]),
        }
        for row in rows
    ]


def network_snapshot() -> dict[str, Any]:
    hostname = socket.gethostname() or None
    label = str(hostname or "").split(".", 1)[0]
    try:
        ports = shared_kiss_ports()
    except Exception:
        ports = []
    return {
        "hostname": hostname,
        "ipv4": primary_ipv4(),
        "ipv6": primary_ipv6(),
        "mdns_name": f"{label}.local" if label else None,
        "kiss_ports": ports,
    }
