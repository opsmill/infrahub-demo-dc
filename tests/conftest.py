"""Fixtures shared by the whole test session.

The Infrahub deployment and everything built on it lives in ``tests/integration/conftest.py``; this
module holds only path fixtures that do not need a running instance, so the unit and smoke suites can
use them without pulling the container fixtures into scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CURRENT_DIR = Path(__file__).parent


@pytest.fixture(scope="session")
def root_dir() -> Path:
    """Repository root.

    Returns:
        The absolute path to the repository root.
    """
    return CURRENT_DIR.parent.resolve()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory holding static test fixtures.

    Returns:
        The absolute path to ``tests/fixtures``.
    """
    return CURRENT_DIR / "fixtures"


@pytest.fixture(scope="session")
def schema_dir(root_dir: Path) -> Path:
    """Directory holding the project schema files.

    Args:
        root_dir: Repository root.

    Returns:
        The absolute path to ``schemas``.
    """
    return root_dir / "schemas"


@pytest.fixture(scope="session")
def data_dir(root_dir: Path) -> Path:
    """Directory holding the object data files.

    Args:
        root_dir: Repository root.

    Returns:
        The absolute path to ``objects``.
    """
    return root_dir / "objects"
