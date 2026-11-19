"""Start the APRSBox window without a console (pythonw APRSBox.pyw [--minimized] [--page NAME])."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.desktop.__main__ import run  # noqa: E402

sys.exit(run())
