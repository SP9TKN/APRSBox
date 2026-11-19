"""Opening SQLite connections stays cheap: they are opened for nearly every query."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import db

RUNTIME_DIRECTORIES = (
    ("APRSBOX_DB_PATH", "data/aprsbox.db"),
    ("APRSBOX_LOG_DIR", "logs"),
    ("APRSBOX_CONFIG_DIR", "config"),
    ("APRSBOX_BACKUPS_DIR", "backups"),
)


@pytest.fixture()
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, sub in RUNTIME_DIRECTORIES:
        monkeypatch.setenv(name, str(tmp_path / sub))
    return tmp_path


def test_connect_makes_the_runtime_directories_once(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[Path] = []
    original = Path.mkdir

    def mkdir(self: Path, *args: object, **kwargs: object) -> None:
        made.append(self)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    for _ in range(3):
        db.connect().close()
    assert sorted(path.name for path in made) == ["backups", "config", "data", "logs"]  # the first time only
    assert all((runtime_root / name).is_dir() for name in ("backups", "config", "data", "logs"))


def test_connect_makes_a_removed_data_directory_again(runtime_root: Path) -> None:
    db.connect().close()
    shutil.rmtree(runtime_root / "data")
    connection = db.connect()  # the directories were remembered as made: opening fails, then makes them
    try:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()
    assert (runtime_root / "data").is_dir()


def test_another_runtime_root_gets_its_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for root in (tmp_path / "one", tmp_path / "two"):
        for name, sub in RUNTIME_DIRECTORIES:
            monkeypatch.setenv(name, str(root / sub))
        db.connect().close()
        assert all((root / name).is_dir() for name in ("backups", "config", "data", "logs"))
