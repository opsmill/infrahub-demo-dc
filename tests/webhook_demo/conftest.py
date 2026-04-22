"""Integration test bootstrap for the webhook demo.

These tests exercise real Infrahub + docker stack (per constitution Principle
II — no SDK mocking). They are gated by `RUN_INTEGRATION=1` so the default
pytest run stays fast; CI activates this lane explicitly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts" / "webhook_demo"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _integration_enabled() -> bool:
    return os.getenv("RUN_INTEGRATION", "0") == "1"


def _e2e_enabled() -> bool:
    return os.getenv("RUN_E2E", "0") == "1"


skip_unless_integration = pytest.mark.skipif(
    not _integration_enabled(),
    reason="set RUN_INTEGRATION=1 to run webhook-demo integration tests",
)

skip_unless_e2e = pytest.mark.skipif(
    not _e2e_enabled(),
    reason="set RUN_E2E=1 to run end-to-end webhook-demo test (requires full stack up)",
)
