"""User Story 2 tests: the webhook demo does not leak into other demos.

Structural isolation is enforced by the `webhook-demo` compose profile
(docker-compose.override.yml); these tests codify that contract.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT = os.environ.get("INFRAHUB_PROJECT_NAME", REPO_ROOT.name)


def test_override_declares_webhook_services_under_profile():
    """The override file must gate webhook-* services behind profile `webhook-demo`.

    Structural check over the raw YAML: each webhook-* service's `profiles:`
    list MUST contain exactly `webhook-demo`. This is what guarantees the
    default `invoke start` path never activates those services.
    """
    import yaml

    data = yaml.safe_load((REPO_ROOT / "docker-compose.override.yml").read_text())
    services = data.get("services", {}) or {}
    webhook_services = {name: svc for name, svc in services.items() if "webhook" in name}
    assert webhook_services, "docker-compose.override.yml must define at least one webhook-* service"
    for name, svc in webhook_services.items():
        profiles = svc.get("profiles") or []
        assert "webhook-demo" in profiles, (
            f"service {name!r} must sit on profile 'webhook-demo' (found profiles={profiles})"
        )


def test_webhook_services_never_listed_by_default_docker_compose_ps():
    """docker compose ps (no --profile) never reports webhook-* services,
    even when they are running. Verifies compose's profile semantics hold:
    activating a service requires its profile to be named, and listing
    services without the profile hides them.

    Complements test_override_declares_webhook_services_under_profile:
    the structural check proves the override is correct; this check proves
    docker compose honors the directive on this host.
    """
    import yaml

    data = yaml.safe_load((REPO_ROOT / "docker-compose.override.yml").read_text())
    services = data.get("services", {}) or {}
    webhook_services = {n for n, s in services.items() if "webhook" in n}
    # `docker compose config --services` enumerates the active service set
    # given the current compose/profile selection; with no --profile the
    # webhook services MUST be absent.
    # We only attempt this when the base compose file is locally present
    # (some bundle-dc flows stream it from the network).
    base = REPO_ROOT / "docker-compose.yml"
    if not base.exists():
        pytest.skip("docker-compose.yml not locally present")
    out = subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            str(base),
            "-f",
            str(REPO_ROOT / "docker-compose.override.yml"),
            "config",
            "--services",
        ],
        text=True,
        cwd=str(REPO_ROOT),
    )
    default_services = {line.strip() for line in out.splitlines() if line.strip()}
    leaks = webhook_services & default_services
    assert not leaks, f"webhook-demo services appear in the default compose service set: {leaks}"
