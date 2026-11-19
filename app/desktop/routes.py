"""Map the web GUI's hrefs (still produced by the service layer) to window pages."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

SIMPLE_PAGES = {
    "/dashboard": "dashboard",
    "/map": "map",
    "/stations": "stations",
    "/traffic": "traffic",
    "/alerts": "alerts",
    "/alerts/send": "alert-send",
    "/band-condition": "band-condition",
    "/statistics": "statistics",
    "/settings/modems": "modems",
    "/station": "station",
    "/wx": "wx",
    "/messages": "messages",
    "/notifications": "notifications",
    "/objects": "objects",
    "/items": "objects",
    "/bulletins": "bulletins",
    "/digi-flows": "digi-flows",
    "/digi-flows/new": "digi-flow-edit",
    "/logs": "logs",
    "/admin/users": "users",
    "/settings": "settings",
    "/changelog": "changelog",
    "/igate": "modems",
}


def route_for_href(href: str | None) -> tuple[str, Any] | None:
    text = str(href or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    path = parsed.path.rstrip("/") or "/"
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
    if path.startswith("/stations/"):
        return "station-detail", unquote(path[len("/stations/"):])
    if path.startswith("/traffic/frames/"):
        return _int_route("frame-detail", path[len("/traffic/frames/"):])
    if path.startswith("/alerts/") and path not in SIMPLE_PAGES:
        return _int_route("alert-detail", path[len("/alerts/"):])
    if path.startswith("/digi-flows/") and path not in SIMPLE_PAGES:
        return _int_route("digi-flow-edit", path[len("/digi-flows/"):])
    page = SIMPLE_PAGES.get(path)
    if page is None:
        return None
    if path == "/items":
        return page, {"tab": "items", **({"edit": query["edit"]} if "edit" in query else {})}
    if query:
        return page, query
    return page, None


def _int_route(page: str, text: str) -> tuple[str, Any] | None:
    try:
        return page, int(text.split("/", 1)[0])
    except ValueError:
        return None
