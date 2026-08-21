"""Pytest bootstrap: put listener/src on sys.path so `webhook_listener`
imports resolve without installing the listener as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

LISTENER_ROOT = Path(__file__).resolve().parent.parent
LISTENER_SRC = LISTENER_ROOT / "src"
if str(LISTENER_SRC) not in sys.path:
    sys.path.insert(0, str(LISTENER_SRC))
