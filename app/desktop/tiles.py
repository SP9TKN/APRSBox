"""Map tile loading for the native map.

Sources come from the map sources configured in Settings (same table as the
web GUI).  A source with the local cache/proxy enabled is served through
``app.services.map_tile_proxy`` exactly like the web proxy endpoint.  Other
sources are fetched directly; the desktop keeps its own disk copy of them so
already-viewed areas stay available offline (the web GUI relied on the browser
cache for that).
"""

from __future__ import annotations

import math
import re
import threading
import time
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRectF, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap

from app import get_version
from app.config import settings
from app.db import get_app_setting, log_event, set_app_setting
from app.desktop.geo import haversine_m

DESKTOP_TILE_CACHE_DIR = "desktop-map-tiles"
DESKTOP_TILE_CACHE_MAX_AGE_S = 14 * 24 * 3600
# Disk copy limit, set in Settings -> Desktop application (the user's choice: 2 GB by default). Over
# it, the tiles lying wholly outside the station's listening area go first - a circle around My
# Station with the APRS-IS filter radius, as for CWOP - then the oldest ones, down to 90 % of the
# limit so the check does not run for every new tile.
TILE_CACHE_LIMIT_SETTING_KEY = "desktop.map_tile_cache_limit_mb"
# "Fast map" (the user's choice in Settings): many tiles kept in RAM and never given back; off = a light app.
FAST_MAP_SETTING_KEY = "desktop.map_fast_tiles"
TILE_CACHE_LIMIT_OPTIONS_MB = (512, 1024, 2048, 3072, 5120, 10240)
DEFAULT_TILE_CACHE_LIMIT_MB = 2048
TILE_CACHE_PRUNE_TO = 0.9
TILE_CACHE_CHECK_EVERY_BYTES = 32 * 1024 * 1024  # downloaded since the last check
TILE_CACHE_FIRST_CHECK_S = 60.0  # after start, once the window is up
TILE_FETCH_TIMEOUT_S = 8.0
_TOKEN_RE_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass(slots=True)
class TileSource:
    source_id: int = 0
    name: str = ""
    url_template: str = ""
    attribution: str = ""
    subdomains: list[str] = field(default_factory=list)
    min_zoom: int = 0
    max_zoom: int = 19
    local_cache: bool = False
    api_key: str = ""

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.url_template}:{int(self.local_cache)}"

    @property
    def valid(self) -> bool:
        return bool(self.url_template)


def load_active_tile_source(source_id: int | None = None) -> TileSource:
    """The map source picked on the map's top bar while it is enabled, else the default one
    (same choice as resolve_active_tile_layer)."""
    from app.services.map_service import get_default_map_source, get_map_source

    source = None
    if source_id:
        try:
            chosen = get_map_source(int(source_id))
        except Exception:
            chosen = None
        if chosen and chosen.get("enabled"):
            source = chosen
    if source is None:
        try:
            source = get_default_map_source()
        except Exception:
            source = None
    if not source:
        return TileSource()
    subdomains = [token for token in re.split(r"[,\s]+", str(source.get("subdomains") or "")) if token]
    return TileSource(
        source_id=int(source.get("id") or 0),
        name=str(source.get("name") or ""),
        url_template=str(source.get("url_template") or ""),
        attribution=_strip_html(str(source.get("attribution") or "")),
        subdomains=subdomains,
        min_zoom=int(source.get("min_zoom") or 0),
        max_zoom=int(source.get("max_zoom") or 19),
        local_cache=bool(source.get("local_cache_enabled")),
        api_key=str(source.get("api_key") or ""),
    )


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&copy;", "©").replace("&amp;", "&").strip()


def _replace_token(template: str, token: str, value: str) -> str:
    pattern = _TOKEN_RE_CACHE.get(token)
    if pattern is None:
        pattern = re.compile(r"\{\s*" + re.escape(token) + r"\s*\}", re.IGNORECASE)
        _TOKEN_RE_CACHE[token] = pattern
    return pattern.sub(value, template)


def build_tile_url(source: TileSource, z: int, x: int, y: int) -> str:
    template = source.url_template.replace("{apiKey}", quote(source.api_key, safe=""))
    template = template.replace("%7B", "{").replace("%7b", "{").replace("%7D", "}").replace("%7d", "}")
    subdomain = source.subdomains[(x + y) % len(source.subdomains)] if source.subdomains else "a"
    url = _replace_token(template, "s", subdomain)
    url = _replace_token(url, "z", str(z))
    url = _replace_token(url, "x", str(x))
    url = _replace_token(url, "y", str(y))
    url = _replace_token(url, "r", "")
    return url


def tile_cache_root() -> Path:
    return Path(settings.cache_dir) / DESKTOP_TILE_CACHE_DIR


def tile_cache_limit_mb() -> int:
    try:
        value = int(get_app_setting(TILE_CACHE_LIMIT_SETTING_KEY) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value in TILE_CACHE_LIMIT_OPTIONS_MB else DEFAULT_TILE_CACHE_LIMIT_MB


def fast_map_enabled() -> bool:
    try:
        return str(get_app_setting(FAST_MAP_SETTING_KEY) or "").strip() == "1"
    except Exception:  # noqa: BLE001 - no database yet: the light default
        return False


def set_fast_map(enabled: bool) -> None:
    set_app_setting(FAST_MAP_SETTING_KEY, "1" if enabled else "0")
    if _SHARED is not None:
        _SHARED.set_fast(enabled)


def set_tile_cache_limit_mb(value: int) -> None:
    if int(value) not in TILE_CACHE_LIMIT_OPTIONS_MB:
        raise ValueError("Choose a limit from the list.")
    set_app_setting(TILE_CACHE_LIMIT_SETTING_KEY, str(int(value)))
    start_tile_cache_check()  # a lower limit takes effect right away


def tile_cache_size_bytes() -> int:
    total = 0
    for path in tile_cache_root().rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def tile_distance_km(z: int, x: int, y: int, latitude: float, longitude: float) -> float:
    """Distance from a point to the nearest edge of a tile (0 when the point lies on the tile)."""
    count = 1 << z
    west = x / count * 360.0 - 180.0
    east = (x + 1) / count * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / count))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / count))))
    nearest_lat = min(max(latitude, south), north)
    nearest_lon = min(max(longitude, west), east)
    return haversine_m(latitude, longitude, nearest_lat, nearest_lon) / 1000.0


def _cached_tile_zxy(path: Path, root: Path) -> tuple[int, int, int] | None:
    parts = path.relative_to(root).parts  # <source>/<z>/<x>/<y>.<ext>
    if len(parts) != 4:
        return None
    try:
        return int(parts[1]), int(parts[2]), int(parts[3].split(".", 1)[0])
    except ValueError:
        return None


def prune_tile_cache(root: Path, *, limit_bytes: int | None = None,
                     center: tuple[float, float] | None = None, radius_km: float | None = None) -> dict[str, int]:
    """Keep the disk copy of the map tiles under ``limit_bytes`` (default: the setting): tiles wholly
    outside the circle (center, radius_km) are removed first, then the oldest ones (by download time)."""
    if limit_bytes is None:
        limit_bytes = tile_cache_limit_mb() * 2**20
    entries: list[tuple[Path, int, float]] = []
    total = 0
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        entries.append((path, stat.st_size, stat.st_mtime))
    result = {"total_bytes": total, "removed": 0, "removed_outside": 0, "freed_bytes": 0}
    if total <= limit_bytes:
        return result

    def inside(path: Path) -> bool:
        if center is None or radius_km is None:
            return True  # no station area: only the age decides
        tile = _cached_tile_zxy(path, root)
        return tile is not None and tile_distance_km(*tile, center[0], center[1]) <= radius_km

    target = int(limit_bytes * TILE_CACHE_PRUNE_TO)
    ranked = sorted(((inside(path), mtime, path, size) for path, size, mtime in entries), key=lambda item: item[:2])
    for is_inside, _mtime, path, size in ranked:
        if total <= target:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        result["removed"] += 1
        result["removed_outside"] += 0 if is_inside else 1
        result["freed_bytes"] += size
        parent = path.parent
        while parent != root:  # drop the folders left empty
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    result["total_bytes"] = total
    log_event(
        "INFO",
        "map",
        f"Map tile cache over {limit_bytes // 2**20} MB: removed {result['removed']} tiles "
        f"({result['removed_outside']} outside the listening area), {result['freed_bytes'] // 2**20} MB freed, "
        f"now {total // 2**20} MB",
    )
    return result


def station_listening_area() -> tuple[tuple[float, float] | None, float | None]:
    """My Station's position and the APRS-IS filter radius (the station's listening area)."""
    from app.services.content import get_station_settings
    from app.services.cwop import aprsis_range_km

    try:
        station = get_station_settings(include_tx_runtime=False)
        latitude = float(station.get("latitude"))
        longitude = float(station.get("longitude"))
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None, None
    return (latitude, longitude), float(aprsis_range_km())


_cache_lock = threading.Lock()
_cache_written = 0
_cache_checking = False


def _note_cache_write(size: int) -> None:
    global _cache_written
    with _cache_lock:
        _cache_written += size
        due = _cache_written >= TILE_CACHE_CHECK_EVERY_BYTES and not _cache_checking
    if due:
        start_tile_cache_check()


def start_tile_cache_check(delay_s: float = 0.0) -> None:
    """Check the size of the disk copy in the background (one check at a time)."""
    global _cache_checking, _cache_written
    with _cache_lock:
        if _cache_checking:
            return
        _cache_checking = True
        _cache_written = 0

    def run() -> None:  # pragma: no cover - background thread
        global _cache_checking
        try:
            if delay_s:
                time.sleep(delay_s)
            center, radius_km = station_listening_area()
            prune_tile_cache(tile_cache_root(), center=center, radius_km=radius_km)
        except Exception as exc:  # noqa: BLE001 - never stops the map
            try:
                log_event("WARNING", "map", f"Map tile cache check failed: {exc}")
            except Exception:
                pass
        finally:
            with _cache_lock:
                _cache_checking = False

    threading.Thread(target=run, name="aprsbox-tile-cache", daemon=True).start()


# "Wysokosci (AWS)" (the user's choice): AWS Terrain Tiles carry the height in the pixel colours
# (terrarium: R * 256 + G + B / 256 - 32768 metres). APRSBox paints them itself: one fixed colour per
# height (the same height looks the same on every tile) multiplied by Esri's relief shading. A painted
# tile is kept for good - the terrain does not change - and only the disk limit can remove it.
ELEVATION_URL_MARKER = "/terrarium/"
ELEVATION_HILLSHADE_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/"
                           "MapServer/tile/{z}/{y}/{x}")
ELEVATION_HILLSHADE_MAX_ZOOM = 16
ELEVATION_STYLE = "v1"  # part of the cache folder: new colours make the tiles be painted again
ELEVATION_RAMP: tuple[tuple[int, tuple[int, int, int]], ...] = (
    (100, (38, 115, 60)), (200, (86, 156, 78)), (250, (136, 186, 92)), (300, (186, 208, 108)),
    (400, (232, 224, 128)), (500, (228, 190, 108)), (700, (204, 146, 86)), (900, (170, 108, 66)),
    (1100, (138, 92, 72)), (1300, (160, 150, 150)), (1550, (240, 240, 240)),
)
_elevation_table: array | None = None
_elevation_table_lock = threading.Lock()


def is_elevation_source(source: TileSource) -> bool:
    return ELEVATION_URL_MARKER in source.url_template.lower()


def elevation_colour(metres: float) -> tuple[int, int, int]:
    clamped = min(max(metres, ELEVATION_RAMP[0][0]), ELEVATION_RAMP[-1][0])
    for (low, low_colour), (high, high_colour) in zip(ELEVATION_RAMP, ELEVATION_RAMP[1:]):
        if low <= clamped <= high:
            share = (clamped - low) / (high - low)
            return tuple(round(a + (b - a) * share) for a, b in zip(low_colour, high_colour))  # type: ignore[return-value]
    return ELEVATION_RAMP[-1][1]


def _elevation_colour_table() -> array:
    """(R << 8 | G) of a terrarium pixel -> 0xFFRRGGBB; built once (0.2 s), 256 KB."""
    global _elevation_table
    with _elevation_table_lock:
        if _elevation_table is None:
            table = array("I", bytes(4 * 65536))
            for key in range(65536):
                red, green, blue = elevation_colour(key - 32768)
                table[key] = 0xFF000000 | (red << 16) | (green << 8) | blue
            _elevation_table = table
        return _elevation_table


def render_elevation_tile(terrarium: bytes, hillshade: bytes | None) -> bytes:
    """A terrarium tile painted with the height colours and the relief shading, as JPEG."""
    image = QImage.fromData(terrarium)
    if image.isNull():
        raise ValueError("Elevation tile is not an image.")
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    width, height = image.width(), image.height()
    pixels = array("I")
    pixels.frombytes(bytes(image.constBits())[: width * height * 4])
    table = _elevation_colour_table()
    painted = array("I", [table[(value >> 8) & 0xFFFF] for value in pixels])
    result = QImage(painted.tobytes(), width, height, width * 4, QImage.Format.Format_RGB32).copy()
    shade = QImage.fromData(hillshade) if hillshade else QImage()
    if not shade.isNull():
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.drawImage(QRectF(0, 0, width, height), shade)
        painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    result.save(buffer, "JPG", 85)
    buffer.close()
    return bytes(data.data())


def _download(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": f"APRSBox/{get_version()} (desktop)",
            "Accept": "image/png,image/webp,image/*,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=TILE_FETCH_TIMEOUT_S) as response:
            return response.read()
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise OSError(str(exc)) from exc


def _write_cache(path: Path, body: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_bytes(body)
        temp_path.replace(path)
    except OSError:
        return
    _note_cache_write(len(body))


def fetch_elevation_tile_bytes(source: TileSource, z: int, x: int, y: int) -> bytes:
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{source.source_id}-{source.name}-{ELEVATION_STYLE}")[:80]
    cache_path = tile_cache_root() / safe_key / str(z) / str(x) / f"{y}.jpg"
    try:
        return cache_path.read_bytes()  # painted once, kept: no refresh after 14 days here
    except OSError:
        pass
    terrarium = _download(build_tile_url(source, z, x, y))
    hillshade: bytes | None = None
    if z <= ELEVATION_HILLSHADE_MAX_ZOOM:
        try:
            hillshade = _download(ELEVATION_HILLSHADE_URL.format(z=z, x=x, y=y))
        except OSError:
            hillshade = None  # the colours without the shading rather than nothing
    body = render_elevation_tile(terrarium, hillshade)
    if hillshade is not None or z > ELEVATION_HILLSHADE_MAX_ZOOM:
        _write_cache(cache_path, body)  # a tile missing its shading is painted again next time
    return body


def _desktop_cache_path(source: TileSource, z: int, x: int, y: int, url: str) -> Path:
    suffix = Path(urlparse(url).path).suffix.lstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9]{1,8}", suffix or ""):
        suffix = "bin"
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{source.source_id}-{source.name}")[:80] or "source"
    return tile_cache_root() / safe_key / str(z) / str(x) / f"{y}.{suffix}"


def fetch_tile_bytes(source: TileSource, z: int, x: int, y: int) -> bytes:
    """Return tile image bytes (raises OSError/ValueError on failure)."""
    if is_elevation_source(source):
        return fetch_elevation_tile_bytes(source, z, x, y)
    if source.local_cache and source.source_id:
        from app.services.map_tile_proxy import MapTileProxyError, resolve_map_tile

        subdomain = source.subdomains[(x + y) % len(source.subdomains)] if source.subdomains else ""
        try:
            result = resolve_map_tile(source_id=source.source_id, z=z, x=x, y=y, requested_subdomain=subdomain)
        except MapTileProxyError as exc:
            raise OSError(exc.message) from exc
        if result.cache_hit and result.cache_path is not None:
            return Path(result.cache_path).read_bytes()
        return result.body or b""

    url = build_tile_url(source, z, x, y)
    cache_path = _desktop_cache_path(source, z, x, y, url)
    cached: bytes | None = None
    try:
        stat = cache_path.stat()
        cached = cache_path.read_bytes()
        if time.time() - stat.st_mtime < DESKTOP_TILE_CACHE_MAX_AGE_S:
            return cached
    except OSError:
        cached = None
    try:
        body = _download(url)
    except OSError:
        if cached:
            return cached  # offline: serve the stale copy
        raise
    _write_cache(cache_path, body)
    return body


class _TileSignals(QObject):
    loaded = Signal(str, int, int, int, object)  # source key, z, x, y, QImage
    failed = Signal(str, int, int, int, str)


class _TileJob(QRunnable):
    def __init__(self, source: TileSource, z: int, x: int, y: int, signals: _TileSignals, generation_ref: Any,
                 generation: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.source = source
        self.z, self.x, self.y = z, x, y
        self.signals = signals
        self.generation_ref = generation_ref
        self.generation = generation

    def run(self) -> None:  # pragma: no cover - worker thread
        try:
            if self.generation_ref() != self.generation:
                self.signals.failed.emit(self.source.key, self.z, self.x, self.y, "__stale__")
                return
            try:
                data = fetch_tile_bytes(self.source, self.z, self.x, self.y)
                image = QImage.fromData(data)
                if image.isNull():
                    raise ValueError("Tile is not an image.")
            except Exception as exc:  # noqa: BLE001 - reported to the GUI thread
                self.signals.failed.emit(self.source.key, self.z, self.x, self.y, str(exc) or exc.__class__.__name__)
                return
            self.signals.loaded.emit(self.source.key, self.z, self.x, self.y, image)
        except RuntimeError:
            # The tile manager was destroyed while the application was closing.
            return


class TileManager(QObject):
    """Shared tile cache + loader (one per application)."""

    tile_ready = Signal()
    status_changed = Signal(str)
    source_changed = Signal()  # another map source: every map using this manager switches with it

    # Decoded tiles kept in RAM (256 KB each): only about what a map shows (a full-screen map is ~54 tiles),
    # the rest come back from the disk cache (the user: a light app; 768 meant up to ~190 MB). The maps
    # also drop them all when no map is on screen (MapView._release_memory_if_unseen).
    MEMORY_TILES = 96
    MEMORY_TILES_FAST = 512  # ~128 MB: places looked at before come back at once

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(6)
        self._signals = _TileSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._signals.failed.connect(self._on_failed)
        self._memory: OrderedDict[tuple[str, int, int, int], QPixmap] = OrderedDict()
        self.fast = False
        self.memory_tiles = self.MEMORY_TILES
        self._pending: set[tuple[str, int, int, int]] = set()
        self._failed_until: dict[tuple[str, int, int, int], float] = {}
        self._generation = 0
        self._lock = threading.Lock()
        self.source = TileSource()
        self.preferred_source_id: int | None = None  # picked on the map, kept in the window settings
        self.consecutive_errors = 0
        self.error_count = 0
        self.load_count = 0
        self.error_active = False
        self.last_error = ""
        self._last_error_report = 0.0
        self._last_recovery_report = 0.0
        self.reload_source()
        start_tile_cache_check(delay_s=TILE_CACHE_FIRST_CHECK_S)

    def reload_source(self) -> None:
        source = load_active_tile_source(self.preferred_source_id)
        if source.key != self.source.key:
            self.source = source
            self._pending.clear()
            self._failed_until.clear()
            self.bump_generation()
            self.consecutive_errors = 0
            if self.error_active:
                self.error_active = False
                self.status_changed.emit("recovered")
            self.source_changed.emit()

    def set_preferred_source(self, source_id: int | None) -> None:
        self.preferred_source_id = int(source_id) if source_id else None
        self.reload_source()

    def bump_generation(self) -> None:
        with self._lock:
            self._generation += 1

    def _current_generation(self) -> int:
        with self._lock:
            return self._generation

    def cached(self, z: int, x: int, y: int) -> QPixmap | None:
        key = (self.source.key, z, x, y)
        pixmap = self._memory.get(key)
        if pixmap is not None:
            self._memory.move_to_end(key)
        return pixmap

    def request(self, z: int, x: int, y: int) -> QPixmap | None:
        if not self.source.valid:
            return None
        key = (self.source.key, z, x, y)
        pixmap = self._memory.get(key)
        if pixmap is not None:
            self._memory.move_to_end(key)
            return pixmap
        if key in self._pending:
            return None
        until = self._failed_until.get(key)
        if until is not None and until > time.monotonic():
            return None
        # The worker serves the disk copy first, so cached areas load offline.
        self._pending.add(key)
        self._pool.start(_TileJob(self.source, z, x, y, self._signals, self._current_generation, self._current_generation()))
        return None

    def _store(self, key: tuple[str, int, int, int], pixmap: QPixmap) -> None:
        self._memory[key] = pixmap
        self._memory.move_to_end(key)
        while len(self._memory) > self.memory_tiles:
            self._memory.popitem(last=False)

    def _on_loaded(self, source_key: str, z: int, x: int, y: int, image: QImage) -> None:
        key = (source_key, z, x, y)
        self._pending.discard(key)
        self._failed_until.pop(key, None)
        if source_key != self.source.key:
            return
        self._store(key, QPixmap.fromImage(image))
        self.load_count += 1
        self.consecutive_errors = 0
        if self.error_active:
            self.error_active = False
            self.status_changed.emit("recovered")
            now = time.monotonic()
            if now - self._last_recovery_report >= 120:
                self._last_recovery_report = now
                log_event(
                    "INFO",
                    "map",
                    f"tile_event=tile_recovered, source={self.source.name or '-'}, provider={self.source.url_template or '-'}, "
                    f"errors={self.error_count}, loads={self.load_count}, user=desktop",
                )
        self.tile_ready.emit()

    def _on_failed(self, source_key: str, z: int, x: int, y: int, message: str) -> None:
        key = (source_key, z, x, y)
        self._pending.discard(key)
        if message == "__stale__" or source_key != self.source.key:
            return
        self._failed_until[key] = time.monotonic() + 30.0
        self.error_count += 1
        self.consecutive_errors += 1
        self.last_error = message
        if not self.error_active and self.consecutive_errors >= 5:
            self.error_active = True
            self.status_changed.emit("unavailable")
            now = time.monotonic()
            if now - self._last_error_report >= 120:
                self._last_error_report = now
                log_event(
                    "WARNING",
                    "map",
                    f"tile_event=tile_error, source={self.source.name or '-'}, provider={self.source.url_template or '-'}, "
                    f"tile={build_tile_url(self.source, z, x, y)}, errors={self.error_count}, loads={self.load_count}, user=desktop",
                )

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self.bump_generation()
        self._pool.clear()
        self._pool.waitForDone(timeout_ms)

    def set_fast(self, enabled: bool) -> None:
        self.fast = bool(enabled)
        self.memory_tiles = self.MEMORY_TILES_FAST if self.fast else self.MEMORY_TILES
        while len(self._memory) > self.memory_tiles:
            self._memory.popitem(last=False)

    def clear_memory(self) -> None:
        self._memory.clear()
        self._failed_until.clear()


_SHARED: TileManager | None = None


def shared_tile_manager() -> TileManager:
    global _SHARED
    if _SHARED is None:
        _SHARED = TileManager()
        _SHARED.set_fast(fast_map_enabled())
    return _SHARED


def shutdown_shared_tile_manager(timeout_ms: int = 3000) -> None:
    """Stop pending tile downloads at exit (does not create the manager)."""
    if _SHARED is not None:
        _SHARED.shutdown(timeout_ms)
