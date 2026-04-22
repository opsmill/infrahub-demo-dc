"""User Story 3 tests: each externality can be cycled independently.

Structural tests over tasks.py + docker-compose.override.yml prove the
invoke-task surface exists; the runtime cycling tests (gated by
RUN_INTEGRATION=1) exercise each one against a live stack.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .conftest import skip_unless_integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_each_externality_has_both_up_and_down_task():
    """Every webhook-demo externality exposes both -up and -down invoke tasks."""
    output = subprocess.check_output(
        ["uv", "run", "invoke", "--list"],
        cwd=str(REPO_ROOT),
        text=True,
    )
    task_names = {line.split()[0] for line in output.splitlines() if line.strip().startswith("demo-webhook-")}
    # lab and listener must each have up + down. (gitea dropped per the
    # design simplification — local /upstream mount replaces it.)
    for base in ("demo-webhook-lab", "demo-webhook-listener"):
        assert f"{base}-up" in task_names, f"{base}-up missing from invoke --list"
        assert f"{base}-down" in task_names, f"{base}-down missing from invoke --list"
    # Aggregate commands also present.
    for agg in (
        "demo-webhook-up",
        "demo-webhook-down",
        "demo-webhook-destroy",
        "demo-webhook-sync",
        "demo-webhook-seed",
    ):
        assert agg in task_names, f"{agg} missing from invoke --list"


@skip_unless_integration
def test_listener_cycle_preserves_lab():
    """demo-webhook-listener-down + -up brings the listener back; fw1 untouched."""
    subprocess.check_call(["uv", "run", "invoke", "demo-webhook-listener-down"], cwd=str(REPO_ROOT))
    # fw1 still present
    fw1 = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=clab-cwdemo-webhook-demo-fw1"],
        text=True,
    ).strip()
    assert "clab-cwdemo-webhook-demo-fw1" in fw1, "fw1 should persist across listener cycle"

    t0 = time.monotonic()
    subprocess.check_call(["uv", "run", "invoke", "demo-webhook-listener-up"], cwd=str(REPO_ROOT))
    elapsed = time.monotonic() - t0
    assert elapsed < 60, f"listener-up must finish in ≤60s (SC-007); took {elapsed:.1f}s"

    # Listener healthy again
    import httpx

    r = httpx.get("http://127.0.0.1:8001/healthz", timeout=5)
    assert r.status_code == 200
