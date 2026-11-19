"""Geometry helpers for the native map (Web Mercator, distances, locators, PHG).

Ports of the formulas used by the web map (Leaflet + app/static/js/map.js),
kept free of Qt so they can be unit tested directly.
"""

from __future__ import annotations

import math
from typing import Iterable

TILE_SIZE = 256
EARTH_RADIUS_M = 6371000.0
MAX_LATITUDE = 85.0511287798


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_longitude(longitude: float) -> float:
    return ((float(longitude) + 180.0) % 360.0 + 360.0) % 360.0 - 180.0


def world_size(zoom: float) -> float:
    return TILE_SIZE * (2.0 ** zoom)


def project(latitude: float, longitude: float, zoom: float) -> tuple[float, float]:
    """Latitude/longitude -> global pixel coordinates at ``zoom`` (Leaflet EPSG:3857)."""
    size = world_size(zoom)
    lat = clamp(float(latitude), -MAX_LATITUDE, MAX_LATITUDE)
    x = (float(longitude) + 180.0) / 360.0 * size
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * size
    return x, y


def unproject(x: float, y: float, zoom: float) -> tuple[float, float]:
    size = world_size(zoom)
    longitude = x / size * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / size
    latitude = math.degrees(math.atan(math.sinh(n)))
    return latitude, longitude


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_point(latitude: float, longitude: float, bearing: float, distance_m: float) -> tuple[float, float]:
    angular = distance_m / EARTH_RADIUS_M
    theta = math.radians(bearing)
    phi1 = math.radians(latitude)
    lambda1 = math.radians(longitude)
    phi2 = math.asin(math.sin(phi1) * math.cos(angular) + math.cos(phi1) * math.sin(angular) * math.cos(theta))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(angular) * math.cos(phi1),
        math.cos(angular) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), normalize_longitude(math.degrees(lambda2))


def circle_points(latitude: float, longitude: float, radius_m: float, samples: int = 72) -> list[tuple[float, float]]:
    points = [destination_point(latitude, longitude, index * 360.0 / samples, radius_m) for index in range(samples)]
    points.append(points[0])
    return points


def phg_direction_azimuth(direction: object) -> float | None:
    """Same mapping as map.js ``phgDirectionAzimuth`` (None = omni)."""
    if direction is None:
        return None
    normalized = str(direction).strip().upper()
    if not normalized or normalized in {"OMNI", "0"}:
        return None
    codes = {"1": 45, "2": 90, "3": 135, "4": 180, "5": 225, "6": 270, "7": 315, "8": 0}
    if normalized in codes:
        return float(codes[normalized])
    names = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
    if normalized in names:
        return float(names[normalized])
    return None


def phg_cardioid_points(latitude: float, longitude: float, azimuth: float, radius_m: float,
                        samples: int = 96) -> list[tuple[float, float]]:
    """Directional PHG coverage (map.js ``buildPhgCardioidPoints``)."""
    points: list[tuple[float, float]] = []
    backward = radius_m / 3
    cardioid_radius = radius_m + backward
    azimuth_rad = math.radians(azimuth)
    cos_a, sin_a = math.cos(azimuth_rad), math.sin(azimuth_rad)
    for index in range(samples):
        theta = index / samples * math.pi * 2
        radial = cardioid_radius * ((1 + math.cos(theta)) / 2)
        local_x = radial * math.sin(theta)
        local_y = radial * math.cos(theta) - backward
        rotated_x = local_x * cos_a + local_y * sin_a
        rotated_y = -local_x * sin_a + local_y * cos_a
        distance = math.hypot(rotated_x, rotated_y)
        if distance < 0.01:
            points.append((latitude, longitude))
            continue
        bearing = math.degrees(math.atan2(rotated_x, rotated_y))
        points.append(destination_point(latitude, longitude, bearing, distance))
    if points:
        points.append(points[0])
    return points


def maidenhead_locator(latitude: float, longitude: float, precision: int = 6) -> str:
    """Maidenhead locator of a position (2, 4, 6 or 8 characters)."""
    lon = normalize_longitude(longitude) + 180.0
    lat = clamp(latitude, -90.0, 89.999999) + 90.0
    locator = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    if precision >= 4:
        locator += str(int((lon % 20) // 2)) + str(int((lat % 10) // 1))
    if precision >= 6:
        locator += chr(97 + int((lon % 2) * 12)) + chr(97 + int((lat % 1) * 24))
    if precision >= 8:
        locator += str(int(((lon % 2) * 12 % 1) * 10)) + str(int(((lat % 1) * 24 % 1) * 10))
    return locator


def maidenhead_grid_spec(zoom: int) -> dict[str, float | int | str]:
    """map.js ``maidenheadGridSpecForZoom``."""
    if zoom <= 5:
        return {"lon_step": 20.0, "lat_step": 10.0, "precision": 2, "size_class": "field"}
    if zoom <= 10:
        return {"lon_step": 2.0, "lat_step": 1.0, "precision": 4, "size_class": "square"}
    if zoom <= 14:
        return {"lon_step": 1 / 12, "lat_step": 1 / 24, "precision": 6, "size_class": "subsquare"}
    return {"lon_step": 1 / 120, "lat_step": 1 / 240, "precision": 8, "size_class": "extended-square"}


def maidenhead_locator_for_cell(longitude_index: int, latitude_index: int, precision: int) -> str:
    """map.js ``maidenheadLocatorForCell`` (labels of the drawn grid)."""
    cells_per_axis = {2: 18, 4: 180, 6: 4320}.get(precision, 43200)
    lon_index = longitude_index % cells_per_axis
    lat_index = max(0, min(cells_per_axis - 1, latitude_index))
    field_divisor = {2: 1, 4: 10, 6: 240}.get(precision, 2400)
    locator = chr(65 + lon_index // field_divisor) + chr(65 + lat_index // field_divisor)
    if precision >= 4:
        square_divisor = {4: 1, 6: 24}.get(precision, 240)
        locator += str((lon_index // square_divisor) % 10)
        locator += str((lat_index // square_divisor) % 10)
    if precision >= 6:
        subsquare_divisor = 1 if precision == 6 else 10
        locator += chr(65 + (lon_index // subsquare_divisor) % 24)
        locator += chr(65 + (lat_index // subsquare_divisor) % 24)
    if precision == 8:
        locator += str(lon_index % 10)
        locator += str(lat_index % 10)
    return locator


def callsign_color_hsl(callsign: str) -> tuple[int, int, int]:
    """map.js ``colorForCallsign``: FNV-1a hash -> neon-ish HSL color."""
    palette = (
        (302, 100, 56), (188, 100, 51), (272, 94, 63), (334, 100, 60), (203, 100, 55), (287, 90, 68),
        (352, 100, 63), (170, 100, 46), (248, 100, 67), (318, 100, 54), (194, 100, 47), (283, 100, 50),
    )
    variants = ((0, 0, 0), (6, -5, 7), (-7, 5, -6))
    value = str(callsign or "").strip().upper()
    hash_value = 2166136261
    for char in value:
        hash_value ^= ord(char)
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    base = palette[hash_value % len(palette)]
    variant = variants[(hash_value >> 8) % len(variants)]
    hue = (base[0] + variant[0] + (((hash_value >> 16) % 5) - 2) + 360) % 360
    saturation = int(clamp(base[1] + variant[1], 86, 100))
    lightness = int(clamp(base[2] + variant[2], 44, 72))
    return hue, saturation, lightness


def bounds_of(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) of latitude/longitude pairs."""
    lats: list[float] = []
    lons: list[float] = []
    for lat, lon in points:
        lats.append(lat)
        lons.append(lon)
    if not lats:
        return None
    return min(lats), min(lons), max(lats), max(lons)


def fit_zoom(bounds: tuple[float, float, float, float], width: int, height: int, *, padding: int = 20,
             min_zoom: int = 0, max_zoom: int = 18) -> int:
    south, west, north, east = bounds
    for zoom in range(max_zoom, min_zoom - 1, -1):
        x1, y1 = project(north, west, zoom)
        x2, y2 = project(south, east, zoom)
        if abs(x2 - x1) <= max(1, width - 2 * padding) and abs(y2 - y1) <= max(1, height - 2 * padding):
            return zoom
    return min_zoom


def point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < cross:
                inside = not inside
        j = i
    return inside
