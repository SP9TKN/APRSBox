"""Map backgrounds added once to every database by the desktop (also a fresh one from the ZIP)."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("PySide6")


@contextlib.contextmanager
def temporary_database() -> Iterator[None]:
    from app.db import init_db

    with tempfile.TemporaryDirectory() as temp_dir:
        previous = os.environ.get("APRSBOX_DB_PATH")
        os.environ["APRSBOX_DB_PATH"] = str(Path(temp_dir) / "aprsbox.db")
        try:
            init_db()
            yield
        finally:
            if previous is None:
                os.environ.pop("APRSBOX_DB_PATH", None)
            else:
                os.environ["APRSBOX_DB_PATH"] = previous


def test_fresh_database_gets_the_map_presets_once_without_keys() -> None:
    from app.desktop.app import ensure_map_presets
    from app.services.map_service import delete_map_source, list_map_sources

    with temporary_database():
        assert [source["name"] for source in list_map_sources()] == ["OpenStreetMap"]
        ensure_map_presets()
        sources = list_map_sources()
        assert [source["name"] for source in sources] == [
            "OpenStreetMap", "OpenTopoMap", "Wysokości (AWS)", "CARTO ciemna", "CARTO jasna", "Satelita (Esri)"]
        by_name = {source["name"]: source for source in sources}
        assert by_name["OpenStreetMap"]["is_default"]  # the default stays OSM
        assert all(not source["api_key"] for source in sources)  # no key ever comes with them
        assert not by_name["CARTO ciemna"]["enabled"] and not by_name["CARTO jasna"]["enabled"]  # off until a key
        assert by_name["CARTO ciemna"]["subdomains"] == "a,b,c,d" and "{apiKey}" in by_name["CARTO ciemna"]["url_template"]
        assert by_name["Wysokości (AWS)"]["enabled"] and by_name["OpenTopoMap"]["max_zoom"] == 17
        ensure_map_presets()  # once only
        assert len(list_map_sources()) == 6
        delete_map_source(int(by_name["OpenTopoMap"]["id"]))
        ensure_map_presets()
        assert "OpenTopoMap" not in {source["name"] for source in list_map_sources()}  # a deleted one stays deleted


def test_presets_skip_the_maps_a_database_already_has() -> None:
    from app.desktop.app import ensure_map_presets
    from app.services.map_service import list_map_sources, safe_save_map_source

    with temporary_database():
        ok, error, _source_id = safe_save_map_source({
            "name": "Moja topo", "url_template": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
            "min_zoom": "0", "max_zoom": "17", "enabled": "1"})
        assert ok, error
        ensure_map_presets()
        names = [source["name"] for source in list_map_sources()]
        assert "Moja topo" in names and "OpenTopoMap" not in names and len(names) == 6
