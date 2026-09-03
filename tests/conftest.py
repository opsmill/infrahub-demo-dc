"""Fixtures shared by the whole test session.

The Infrahub deployment and everything built on it lives in ``tests/integration/conftest.py``; this
module holds only path fixtures that do not need a running instance, so the unit and smoke suites can
use them without pulling the container fixtures into scope.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

CURRENT_DIR = Path(__file__).parent

# ``infrahub_testcontainers`` registers a pytest plugin -- entry point
# ``pytest-infrahub-performance-test`` -- whose ``pytest_sessionstart`` builds a host profile:
# ``plugin.py:94`` -> ``performance_test.py:44 get_system_stats()`` -> ``host.py:15
# psutil.cpu_freq()``, called unguarded. On Apple Silicon that raises, killing the whole session with
# INTERNALERROR before collection -- unit tests included, because the plugin loads whenever the
# package is installed. ``host.py:19-21`` already read the result as
# ``cpu_freq.current if cpu_freq else None``, so upstream meant it to be nullable and missed that the
# call itself can raise. Remove this once that is fixed upstream.
#
# Catching ``Exception`` is deliberate and not laziness. Two different failures have been observed on
# the same platform family: ``SystemError: <built-in function cpu_freq> returned a result with an
# exception set`` here, and ``RuntimeError: 'voltage-states1-sram' property not found`` elsewhere.
# ``SystemError`` derives from ``Exception`` directly, so a narrower clause listing ``RuntimeError``,
# ``OSError`` and ``AttributeError`` -- which is what one sibling repository has -- does not catch it.
# All three frequency fields are cosmetic telemetry; no reading here is worth an INTERNALERROR.
try:
    import psutil
except ImportError:
    # Nothing left to guard, so say nothing. ``psutil`` is not declared in ``pyproject.toml``; it
    # arrives transitively with ``infrahub_testcontainers``, which is also where the plugin patched
    # below comes from. No ``psutil`` means no plugin and no failure to prevent. An unguarded import,
    # on the other hand, turns a dependency edge this repository does not own into a collection error
    # for the entire suite -- the same class of failure this block exists to remove.
    pass
else:
    _original_cpu_freq = psutil.cpu_freq

    def _cpu_freq_or_none(*args: object, **kwargs: object) -> Any:  # noqa: ANN401 - mirrors psutil's loose return type
        """Report CPU frequency, or ``None`` where the platform cannot.

        Args:
            *args: Passed through to ``psutil.cpu_freq``.
            **kwargs: Passed through to ``psutil.cpu_freq``.

        Returns:
            Whatever ``psutil.cpu_freq`` returns, or ``None`` when it raises.
        """
        try:
            return _original_cpu_freq(*args, **kwargs)
        except Exception:  # noqa: BLE001 - any failure here must degrade to None, never kill the session
            return None

    psutil.cpu_freq = _cpu_freq_or_none

# docker/compose#13899: `up --wait` fails on a project containing a zero-replica service, reporting it
# as a missing dependency. The packaged compose file declares `task-manager-background-svc` with
# `replicas: ${INFRAHUB_TESTING_TASKMGR_BACKGROUND_SVC_REPLICAS:-0}` (container.py:124), so the default
# trips it. Scheduling one replica is harmless -- nothing depends on that service. `setdefault` so an
# explicit value still wins. Drop this when Compose ships the fix, not when a repo stops building a
# custom image: this is a Compose-version issue, not a custom-image one.
os.environ.setdefault("INFRAHUB_TESTING_TASKMGR_BACKGROUND_SVC_REPLICAS", "1")


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
