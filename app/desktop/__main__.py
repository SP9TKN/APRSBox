"""Entry point: ``python -m app.desktop``."""

from __future__ import annotations

import sys


def run() -> int:
    from app.desktop.bootstrap import configure_environment

    configure_environment()
    from app.desktop.app import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
